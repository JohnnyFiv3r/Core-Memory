from __future__ import annotations

from pathlib import Path
from typing import Any
import os
from datetime import datetime
import json

from core_memory.graph.traversal import causal_traverse_chains as causal_traverse
from core_memory.graph.root_cause import normalize_causal_hints
from core_memory.persistence.backend import get_backend_capabilities
from core_memory.persistence.graph.factory import create_graph_backend
from core_memory.persistence.graph.protocol import NullGraphBackend
from core_memory.persistence.source_hydration import hydrate_bead_sources_for_root
from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.normalize import classify_intent
from core_memory.retrieval.semantic_index import (
    _normalize_semantic_mode,
    semantic_lookup,
    semantic_unavailable_payload,
)
from core_memory.retrieval.visible_corpus import build_visible_corpus
from core_memory.schema.normalization import normalize_bead_type, normalize_relation_type, relation_family
from core_memory.claim.retrieval_planner import plan_retrieval_mode, boost_claim_results
from core_memory.claim.resolver import resolve_all_current_state
from core_memory.claim.answer_policy import score_answer
from core_memory.entity.registry import load_entity_registry
from core_memory.entity.retrieval import infer_query_entity_context, expand_query_with_entities
from core_memory.persistence.myelination_manifest import (
    myelination_enabled,
    read_myelination_bead_bonus_map,
)
from core_memory.retrieval.evidence_scoring import rerank_semantic_rows
from .convergence import run_hybrid_rerank_seeds
from core_memory.config.feature_flags import (
    claim_layer_enabled,
    claim_resolution_enabled,
    claim_retrieval_boost_enabled,
)
from .catalog import build_catalog


NON_FULL_GROUNDING_RELATIONSHIPS = {"follows", "precedes", "associated_with"}


def _env_int(name: str, default: int) -> int:
    """Read a positive int tunable from the environment, falling back to default."""
    try:
        v = int(os.getenv(name, "") or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# Causal traversal tunables. Defaults preserve prior behaviour; the env knobs let
# the seed breadth, depth, and chain-merge budget be tuned without a redeploy.
#   _TRACE_SEED_ANCHORS:   how many top semantic anchors seed graph traversal
#                          (was a hardcoded 5 — too few when the gold-adjacent
#                          bead ranks just outside the top 5).
#   _TRACE_MAX_DEPTH:      max traversal hops from each seed.
#   _TRACE_MAX_CHAINS:     max chains returned by traversal.
#   _TRACE_CHAIN_MERGE_BONUS: extra result slots reserved for chain beads beyond
#                          k, so traversal evidence is not crowded out by the k
#                          semantic anchors before it can enter the scored top-k.
def _trace_seed_anchors() -> int:
    return _env_int("CORE_MEMORY_TRACE_SEED_ANCHORS", 12)


def _trace_max_depth() -> int:
    return _env_int("CORE_MEMORY_TRACE_MAX_DEPTH", 6)


def _trace_max_chains() -> int:
    return _env_int("CORE_MEMORY_TRACE_MAX_CHAINS", 12)


def _trace_chain_merge_bonus() -> int:
    return _env_int("CORE_MEMORY_TRACE_CHAIN_MERGE_BONUS", 8)
PUBLIC_HYDRATION_TURN_SOURCES = {"cited_turns", "cited_turns_plus_adjacent"}
CONTROL_CONSTRAINT_KEYS = {
    # Request/benchmark orchestration metadata, not corpus facets.  Treating
    # these as hard bead metadata filters can erase valid retrieval candidates
    # (for example every LoCoMo corpus bead lacks the per-question qa_id).
    "qa_id",
    "benchmark_name",
    "benchmark_phase",
    "recall_scope",
    "selected_answer_effort",
    "retrieval_efforts",
    # Execution-only traversal hint. This must never become a hard corpus
    # metadata constraint; causal wording in recall() should reorder chains,
    # not filter anchors before traversal.
    "structural_hint_relations",
}


def _metadata_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    return {
        str(k): v
        for k, v in dict(constraints or {}).items()
        if str(k or "").strip() and str(k).strip() not in CONTROL_CONSTRAINT_KEYS and str(k).strip() != "require_structural"
    }


def _canonical_semantic_mode() -> str:
    return _normalize_semantic_mode(os.getenv("CORE_MEMORY_CANONICAL_SEMANTIC_MODE"))


def _semantic_failure_response(*, query: str, intent: str, k: int, warnings: list[str], provider: str | None = None) -> dict[str, Any]:
    out = semantic_unavailable_payload(query=query, warnings=warnings, provider=provider)
    out.update(
        {
            "anchors": [],
            "results": [],
            "chains": [],
            "citations": [],
            "confidence": "low",
            "next_action": "ask_clarifying",
            "snapped": {"raw_query": query, "intent": intent, "k": int(k)},
            "grounding": {"required": True, "achieved": False, "level": "none", "reason": "semantic_backend_unavailable"},
            "warnings": list(out.get("warnings") or []),
        }
    )
    return out


def _load_claim_state(root: Path, *, as_of: str | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Load claim state for retrieval planning/policy when claim layer is enabled."""
    warns: list[str] = []
    if not claim_layer_enabled():
        return None, warns
    if not claim_resolution_enabled():
        warns.append("claim_resolution_disabled")
        return None, warns
    try:
        state = resolve_all_current_state(str(root), as_of=as_of)
        if isinstance(state, dict):
            return state, warns
        warns.append("claim_state_unavailable")
    except Exception:
        warns.append("claim_state_error")
    return None, warns


def _query_terms(text: str) -> set[str]:
    import re

    s = str(text or "").lower().replace("_", " ").replace("-", " ")
    stop = {
        "the", "and", "for", "from", "with", "what", "when", "where", "which", "who", "why", "how",
        "did", "does", "was", "were", "has", "have", "had", "take", "took", "kind", "kinds",
        "many", "much", "been", "being", "into", "onto", "about", "that", "this", "these", "those",
        "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
        "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november", "dec", "december",
    }
    out: set[str] = set()
    for tok in re.findall(r"[a-z0-9]+", s):
        if len(tok) < 3 or tok in stop or tok.isdigit():
            continue
        out.add(tok)
        if len(tok) > 3 and tok.endswith("s"):
            out.add(tok[:-1])
    return out


def _claim_query_signal(query: str, retrieval_mode: str) -> bool:
    q = _query_terms(query)
    raw = str(query or "").strip().lower()
    # Claim anchors are most helpful for entity/attribute slots.  Avoid using
    # them as a blanket prefix for temporal/event questions, where same-person
    # claim history can swamp better turn-level retrieval.
    if raw.startswith("when "):
        return False
    if retrieval_mode == "fact_first":
        return True
    attr_terms = {
        "car", "vehicle", "drive", "owned", "own", "broken", "hobby", "identity", "relationship",
        "status", "research", "researched", "topic", "field", "education", "roadtrip", "destination",
        "prius", "painting", "single",
    }
    return bool(q.intersection(attr_terms))


def _claim_anchors_from_state(
    *,
    query: str,
    claim_state: dict[str, Any] | None,
    by_id: dict[str, dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not isinstance(claim_state, dict):
        return []
    slots = claim_state.get("slots") or {}
    if not isinstance(slots, dict):
        return []

    q = _query_terms(query)
    generic_personal = bool(q.intersection({"my", "me", "current", "now"}))
    out: list[dict[str, Any]] = []
    candidate_slot_count = 0

    for key, slot_data in slots.items():
        if not isinstance(slot_data, dict):
            continue
        slot_status = str(slot_data.get("status") or "").strip().lower()
        if slot_status not in {"active", "conflict"}:
            continue
        candidate_slot_count += 1

        claim_candidates: list[dict[str, Any]] = []
        current_claim = slot_data.get("current_claim") or {}
        if isinstance(current_claim, dict) and current_claim:
            claim_candidates.append(dict(current_claim))
        # Attribute/count questions often need historical values, not only the
        # latest resolved state (e.g. "how many cars has Evan owned?").  Include
        # matching slot history as secondary candidates so claim retrieval can
        # surface the original evidence beads instead of collapsing everything
        # to one current value.
        for hist in list(slot_data.get("history") or []):
            if isinstance(hist, dict) and hist:
                hid = str(hist.get("id") or "")
                if hid and any(str(c.get("id") or "") == hid for c in claim_candidates):
                    continue
                claim_candidates.append(dict(hist))
        if not claim_candidates:
            # Conflict slots may not have a stable current claim — use conflict targets when present.
            claim_candidates = [dict(c or {}) for c in list(slot_data.get("conflicts") or []) if isinstance(c, dict)]

        key_s = str(key or "")
        subject, _, slot = key_s.partition(":")
        for claim in claim_candidates:
            if not isinstance(claim, dict):
                continue
            value = claim.get("value")
            value_s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            subject_terms = _query_terms(subject)
            core_signal_terms = _query_terms(" ".join([slot, str(claim.get("claim_kind") or ""), value_s]))
            reason_terms = _query_terms(str(claim.get("reason_text") or ""))
            signal_terms = core_signal_terms | reason_terms
            claim_terms = subject_terms | signal_terms
            overlap = len(q.intersection(claim_terms))
            signal_overlap = len(q.intersection(core_signal_terms))
            subject_overlap = len(q.intersection(subject_terms))
            if overlap == 0 and not (generic_personal and candidate_slot_count == 1):
                continue
            # A shared person/entity name alone is not enough to pin claim
            # anchors; otherwise every "Caroline ..." question gets swamped by
            # unrelated Caroline claims.  Require a slot/value/reason match too.
            if signal_overlap == 0 and not (generic_personal and candidate_slot_count == 1):
                continue

            conf = float(claim.get("confidence") or 0.6)
            if slot_status == "conflict":
                score = min(0.94, max(0.58, conf + (0.03 * overlap) + (0.06 * subject_overlap)))
            else:
                score = min(0.98, max(0.62, conf + (0.03 * overlap) + (0.06 * subject_overlap)))
            source_bid = str(claim.get("source_bead_id") or "").strip()
            bid = source_bid or f"claim:{subject}:{slot}:{str(claim.get('id') or '')}"

            summary_line = f"Current {slot}: {value_s}"
            if slot_status == "conflict":
                conflict_vals = []
                for c in (slot_data.get("conflicts") or []):
                    if not isinstance(c, dict):
                        continue
                    cv = c.get("value")
                    if cv is None:
                        continue
                    conflict_vals.append(str(cv))
                if conflict_vals:
                    summary_line = f"Conflicting {slot}: {' vs '.join(conflict_vals[:3])}"

            by_id.setdefault(bid, {
                "bead": {
                    "id": bid,
                    "title": f"{subject} {slot}".strip(),
                    "type": "context",
                    "summary": [summary_line],
                    "source_turn_ids": list(claim.get("source_turn_ids") or []),
                },
                "source_surface": "claim_state",
                "status": slot_status or "default",
            })
            out.append(
                {
                    "bead_id": bid,
                    "score": score,
                    "anchor_reason": "claim_conflict_state" if slot_status == "conflict" else "claim_current_state",
                    "source_surface": "claim_state",
                    "status": slot_status or "default",
                    "context_bias_score": 0.0,
                    "claim_slot_key": key_s,
                    "claim_id": str(claim.get("id") or ""),
                    "claim_value": value,
                    "claim_status": slot_status,
                    "dia_ids": list(claim.get("source_turn_ids") or []),
                }
            )

    out.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    return out[: max(1, int(limit))]


def _slot_label(slot_key: str) -> str:
    _, _, slot = str(slot_key or "").partition(":")
    s = slot or str(slot_key or "")
    return s.replace("_", " ").strip()


def _claim_answer_candidate(
    *,
    query: str,
    claim_state: dict[str, Any] | None,
    results: list[dict[str, Any]],
    answer_outcome: str,
    as_of: str | None,
) -> dict[str, Any] | None:
    if not isinstance(claim_state, dict):
        return None
    if str(answer_outcome or "") not in {"answer_current", "answer_historical", "answer_partial"}:
        return None

    slots = claim_state.get("slots") or {}
    if not isinstance(slots, dict) or not slots:
        return None

    top = (results or [{}])[0] if (results or []) else {}
    if str((top or {}).get("source_surface") or "") != "claim_state":
        return None

    # Prefer explicit slot metadata from claim-state anchor.
    slot_key = str((top or {}).get("claim_slot_key") or "")
    if not slot_key:
        bead_id = str((top or {}).get("bead_id") or "")
        if bead_id.startswith("claim:"):
            _, subject, slot = bead_id.split(":", 2)
            slot_key = f"{subject}:{slot}"

    slot_data = dict((slots.get(slot_key) or {})) if slot_key else {}
    if not slot_data:
        # fallback: choose best query-overlap slot
        q = _query_terms(query)
        best_key = ""
        best_score = -1
        for k, row in slots.items():
            if str((row or {}).get("status") or "") != "active":
                continue
            claim = (row or {}).get("current_claim") or {}
            terms = _query_terms(" ".join([str(k), str(claim.get("claim_kind") or ""), str(claim.get("value") or "")]))
            score = len(q.intersection(terms))
            if score > best_score:
                best_score = score
                best_key = str(k)
        if best_key:
            slot_key = best_key
            slot_data = dict((slots.get(best_key) or {}))

    claim = dict((slot_data.get("current_claim") or {}))
    if not claim:
        return None

    value = claim.get("value")
    slot_text = _slot_label(slot_key)
    if str(answer_outcome) == "answer_historical" and str(as_of or "").strip():
        text = f"As of {as_of}, {slot_text} was {value}."
    else:
        text = f"Current {slot_text} is {value}."

    return {
        "text": text,
        "slot_key": slot_key,
        "slot": slot_text,
        "value": value,
        "claim_id": str(claim.get("id") or ""),
        "source": "claim_state",
        "as_of": str(as_of or "") or None,
    }


def _normalize_public_hydration_request(hydration: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Normalize public canonical hydration request to supported contract only.

    Public contract:
    - turn_sources: cited_turns | cited_turns_plus_adjacent
    - max_beads
    - adjacent_before
    - adjacent_after
    """
    req = dict(hydration or {})
    warnings: list[str] = []

    turn_sources = req.get("turn_sources")
    before = max(0, int(req.get("adjacent_before") or 0))
    after = max(0, int(req.get("adjacent_after") or 0))
    if turn_sources is True:
        mode_raw = "cited_turns_plus_adjacent" if before or after else "cited_turns"
    else:
        mode_raw = str(turn_sources or "cited_turns").strip().lower()
    if mode_raw not in PUBLIC_HYDRATION_TURN_SOURCES:
        if mode_raw:
            warnings.append(f"hydration_turn_sources_normalized:{mode_raw}->cited_turns")
        mode = "cited_turns"
    else:
        mode = mode_raw

    max_beads = max(1, int(req.get("max_beads") or 10))
    if mode == "cited_turns":
        # adjacency is intentionally off in cited_turns mode
        before = 0
        after = 0

    unsupported = [k for k in req.keys() if k not in {"turn_sources", "max_beads", "adjacent_before", "adjacent_after"}]
    if unsupported:
        warnings.append("hydration_unsupported_fields_ignored")

    normalized = {
        "turn_sources": mode,
        "max_beads": max_beads,
        "adjacent_before": before,
        "adjacent_after": after,
    }
    return normalized, warnings


def _has_non_temporal_structural_edge(chains: list[dict[str, Any]]) -> bool:
    """True when at least one chain includes a non-temporal structural edge.

    v2.1 policy: follows/associated_with-only paths are not sufficient for
    grounding=full.
    """
    for chain in (chains or []):
        for edge in (chain.get("edges") or []):
            rel = str((edge or {}).get("rel") or "").strip().lower()
            edge_class = str((edge or {}).get("class") or (edge or {}).get("edge_class") or "").strip().lower()
            is_structural = (not edge_class) or edge_class == "structural"
            if is_structural and rel and rel not in NON_FULL_GROUNDING_RELATIONSHIPS:
                return True
    return False


def _status_rank(status: str) -> int:
    order = {"promoted": 0, "archived": 1, "candidate": 2, "open": 3}
    return order.get(str(status or "").lower(), 9)


def _relation_summary_from_index(index_payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in list((index_payload or {}).get("associations") or []):
        if not isinstance(row, dict):
            continue
        src = str(row.get("source_bead") or row.get("source_bead_id") or "")
        dst = str(row.get("target_bead") or row.get("target_bead_id") or "")
        if not src or not dst:
            continue
        rel = normalize_relation_type(str(row.get("relationship") or row.get("rel") or ""))

        out.setdefault(src, {"supersedes_count": 0, "superseded_by_count": 0, "contradicts_count": 0})
        out.setdefault(dst, {"supersedes_count": 0, "superseded_by_count": 0, "contradicts_count": 0})
        out[src][rel] = int(out[src].get(rel) or 0) + 1
        out[dst][rel] = int(out[dst].get(rel) or 0) + 1

        if rel == "supersedes":
            out[src]["supersedes_count"] += 1
            out[dst]["superseded_by_count"] += 1
        elif rel == "superseded_by":
            out[src]["superseded_by_count"] += 1
            out[dst]["supersedes_count"] += 1
        elif rel == "contradicts":
            out[src]["contradicts_count"] += 1
            out[dst]["contradicts_count"] += 1

    return out


def _retrieval_value_bonus_from_index(index_payload: dict[str, Any]) -> dict[str, float]:
    bonuses: dict[str, float] = {}
    rows = (index_payload or {}).get("retrieval_value_overrides") or {}
    for row in rows.values() if isinstance(rows, dict) else []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "active").strip().lower() != "active":
            continue
        src = str(row.get("source_bead_id") or "").strip()
        dst = str(row.get("target_bead_id") or "").strip()
        delta = float(row.get("weight_delta") or 0.0)
        if not src or not dst or delta == 0.0:
            continue
        # split bonus across endpoints to keep candidate-level scoring simple
        bonuses[src] = float(bonuses.get(src, 0.0)) + (0.5 * delta)
        bonuses[dst] = float(bonuses.get(dst, 0.0)) + (0.5 * delta)
    return bonuses


CONTINUITY_QUERY_HINTS = (
    "session start",
    "continuity",
    "carry forward",
    "carried forward",
    "left off",
    "working memory",
)


def _is_continuity_query(query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False
    return any(h in q for h in CONTINUITY_QUERY_HINTS)


def _type_priority_rank(bead_type: str, *, continuity_query: bool) -> int:
    t = str(bead_type or "").strip().lower()
    if t in {"decision", "lesson", "outcome", "evidence", "correction", "precedent"}:
        return 0
    if t == "session_start":
        return 1 if continuity_query else 4
    if t in {"goal", "context", "design_principle", "constraint", "failed_hypothesis"}:
        return 2
    return 3


def _session_start_score_adjustment(*, bead_type: str, continuity_query: bool) -> float:
    t = str(bead_type or "").strip().lower()
    if t != "session_start":
        return 0.0
    # session_start stays searchable but is demoted for generic queries.
    return 0.20 if continuity_query else -0.35


def _lexical_rescue(query: str, corpus: list[dict[str, Any]], *, max_add: int = 2) -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    if not q:
        return []
    out: list[dict[str, Any]] = []
    for r in corpus:
        bead = r.get("bead") or {}
        title = str(bead.get("title") or "").strip().lower()
        bid = str(r.get("bead_id") or "").strip().lower()
        incident_id = str(bead.get("incident_id") or "").strip().lower()
        tags = {str(t).strip().lower() for t in (bead.get("tags") or [])}
        topics = {str(t).strip().lower() for t in (bead.get("topics") or [])}
        if q in {title, bid, incident_id} or q in tags or q in topics:
            out.append(
                {
                    "bead_id": str(r.get("bead_id") or ""),
                    "score": 0.54,
                    "semantic_score": 0.0,
                    "status": str(r.get("status") or ""),
                    "source_surface": str(r.get("source_surface") or ""),
                    "anchor_reason": "lexical_rescue",
                    "context_bias_score": 0.0,
                }
            )
    out.sort(key=lambda x: (_status_rank(x.get("status") or ""), str(x.get("bead_id") or "")))
    return out[: max(0, int(max_add))]


def _locomo_dia_ids_from_bead(bead: dict[str, Any], res: dict[str, Any] | None = None) -> list[str]:
    """Expose LoCoMo evidence IDs on public retrieval rows when present.

    Benchmark replay stores dia IDs on bead metadata and/or source turn IDs
    (for example ``locomo:conv-26:D1:3``).  If anchors omit these IDs, the
    benchmark can retrieve the correct bead but still score evidence recall as
    zero and the answerer refuses to cite support.
    """
    out: list[str] = []
    for source in (res or {}, bead, dict((bead or {}).get("metadata") or {})):
        if not isinstance(source, dict):
            continue
        for key in ("dia_ids", "dia_id", "locomo_dia_ids", "locomo_dia_id"):
            value = source.get(key)
            if isinstance(value, (list, tuple, set)):
                out.extend(str(x).strip() for x in value if str(x).strip())
            elif str(value or "").strip():
                out.append(str(value).strip())
    import re

    for turn_id in list((bead or {}).get("source_turn_ids") or []):
        text = str(turn_id or "").strip()
        if not text:
            continue
        # Prefer the LoCoMo evidence form (D<session>:<turn>) wherever it
        # appears. Some replay paths produce IDs like
        # ``locomo:conv-26:D1:3:3``; taking the final component alone yields
        # ``3`` and breaks evidence scoring against gold ``D1:3``.
        match = re.search(r"\bD\d+:\d+\b", text)
        if match:
            out.append(match.group(0))
            continue
        parts = text.split(":")
        if len(parts) >= 3 and parts[0] == "locomo":
            out.append(parts[-1])
    return sorted(set(x for x in out if x))


def _to_anchor(res: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bid = str(res.get("bead_id") or "")
    row = by_id.get(bid) or {}
    bead = row.get("bead") or {}
    metadata = dict(bead.get("metadata") or {})
    source_turn_ids = list(bead.get("source_turn_ids") or [])
    dia_ids = _locomo_dia_ids_from_bead(bead, res)
    return {
        "bead_id": bid,
        "title": str(bead.get("title") or ""),
        "type": str(bead.get("type") or ""),
        "snippet": " ".join((bead.get("summary") or [])[:2]),
        "score": float(res.get("score") or 0.0),
        "semantic_score": float(res.get("semantic_score") if res.get("semantic_score") is not None else (res.get("score") or 0.0)),
        "rank_score": float(res.get("rank_score") or res.get("score") or 0.0),
        "fused_score": float(res.get("fused_score") or 0.0),
        "rerank_seed_score": float(res.get("rerank_seed_score") or 0.0),
        "feature_scores": dict(res.get("feature_scores") or {}),
        "anchor_reason": str(res.get("anchor_reason") or "retrieved"),
        "context_bias_score": float(res.get("context_bias_score") or 0.0),
        "source_surface": str(res.get("source_surface") or row.get("source_surface") or "projection"),
        "status": str(res.get("status") or row.get("status") or ""),
        "session_id": str(row.get("session_id") or bead.get("session_id") or ""),
        "source_turn_ids": source_turn_ids,
        "metadata": metadata,
        "claim_slot_key": str(res.get("claim_slot_key") or ""),
        "claim_id": str(res.get("claim_id") or ""),
        "claim_value": res.get("claim_value"),
        "claim_status": str(res.get("claim_status") or ""),
        "hint_boost": float(res.get("hint_boost") or 0.0),
        "hint_matches": list(res.get("hint_matches") or []),
        "dia_ids": dia_ids,
        "locomo_dia_ids": dia_ids,
    }


def _parse_iso(ts: str) -> datetime | None:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _flatten_metadata_terms(value: Any, *, prefix: str = "") -> list[str]:
    terms: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k or "").strip()
            child_prefix = f"{prefix}.{key}" if prefix else key
            terms.extend(_flatten_metadata_terms(v, prefix=child_prefix))
        return terms
    if isinstance(value, (list, tuple, set)):
        for item in value:
            terms.extend(_flatten_metadata_terms(item, prefix=prefix))
        return terms
    text = str(value or "").strip()
    if not text:
        return terms
    terms.append(text)
    if prefix:
        terms.append(f"{prefix}={text}")
        leaf = prefix.rsplit(".", 1)[-1]
        if leaf != prefix:
            terms.append(f"{leaf}={text}")
    return terms


def _metadata_text_blob(row: dict[str, Any], bead: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("session_id", "incident_id", "created_at"):
        value = str(row.get(key) or bead.get(key) or "").strip()
        if value:
            parts.append(value)
            parts.append(f"{key}={value}")
    for key in ("tags", "topics", "source_turn_ids"):
        parts.extend(_flatten_metadata_terms(row.get(key) or bead.get(key) or [], prefix=key))
    for key in ("metadata", "trace", "source", "projection"):
        parts.extend(_flatten_metadata_terms(bead.get(key) or {}, prefix=key))
    return " ".join(parts).lower()


def _metadata_filter_matches(row: dict[str, Any], bead: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    metadata_blob = _metadata_text_blob(row, bead)
    for key, expected in filters.items():
        k = str(key or "").strip()
        if not k or k == "require_structural":
            continue
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        wanted = [str(v or "").strip().lower() for v in values if str(v or "").strip()]
        if not wanted:
            continue
        if not any((f"{k.lower()}={v}" in metadata_blob) or (v in metadata_blob) for v in wanted):
            return False
    return True


def _apply_typed_filters(
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    projection_created_at: dict[str, str],
    submission: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    s = dict(submission or {})
    warnings: list[str] = []

    incident_id = str(s.get("incident_id") or "").strip()
    scope = str(s.get("scope") or "").strip().lower()
    topic_keys = {str(x).strip().lower() for x in (s.get("topic_keys") or []) if str(x).strip()}
    bead_types = {normalize_bead_type(str(x).strip()) for x in (s.get("bead_types") or []) if str(x).strip()}
    must_terms = [str(x).strip().lower() for x in (s.get("must_terms") or []) if str(x).strip()]
    avoid_terms = [str(x).strip().lower() for x in (s.get("avoid_terms") or []) if str(x).strip()]
    metadata_filters = dict(s.get("metadata") or s.get("metadata_filters") or {})

    tr = dict(s.get("time_range") or {})
    tr_from_raw = str(tr.get("from") or "").strip()
    tr_to_raw = str(tr.get("to") or "").strip()
    tr_from = _parse_iso(tr_from_raw)
    tr_to = _parse_iso(tr_to_raw)
    if (tr_from_raw and tr_from is None) or (tr_to_raw and tr_to is None):
        warnings.append("invalid_time_range_ignored")
        tr_from = None
        tr_to = None

    if not any([incident_id, scope, topic_keys, bead_types, must_terms, avoid_terms, metadata_filters, tr_from, tr_to]):
        return rows, warnings

    out: list[dict[str, Any]] = []
    for r in rows:
        bid = str(r.get("bead_id") or "")
        row = by_id.get(bid) or {}
        bead = row.get("bead") or {}

        if incident_id and str(bead.get("incident_id") or "") != incident_id:
            continue

        if scope:
            bead_scope = str(bead.get("scope") or "").strip().lower()
            # Older/demo beads often do not persist scope.  Treat absent scope as
            # unknown rather than excluding otherwise metadata-matching rows.
            if bead_scope and bead_scope != scope:
                continue

        if topic_keys:
            bead_topics = {str(x).strip().lower() for x in (bead.get("tags") or []) if str(x).strip()}
            bead_topics.update({str(x).strip().lower() for x in (bead.get("topics") or []) if str(x).strip()})
            # Topic hints should constrain rows that have topic metadata, but
            # should not drop legacy rows that only carry equivalent trace metadata.
            if bead_topics and not bead_topics.intersection(topic_keys):
                continue

        if bead_types and normalize_bead_type(str(bead.get("type") or "").strip()) not in bead_types:
            continue

        if tr_from or tr_to:
            bts = _parse_iso(str(projection_created_at.get(bid) or row.get("created_at") or bead.get("created_at") or ""))
            if bts is None:
                continue
            if tr_from and bts < tr_from:
                continue
            if tr_to and bts > tr_to:
                continue

        if metadata_filters and not _metadata_filter_matches(row, bead, metadata_filters):
            continue

        text_blob = " ".join(
            [
                str(bead.get("title") or ""),
                " ".join(str(x) for x in (bead.get("summary") or [])),
                str(bead.get("detail") or ""),
                str(row.get("semantic_text") or ""),
                str(row.get("lexical_text") or ""),
                _metadata_text_blob(row, bead),
            ]
        ).lower()

        if must_terms and not all(t in text_blob for t in must_terms):
            continue
        if avoid_terms and any(t in text_blob for t in avoid_terms):
            continue

        out.append(r)

    return out, warnings


def _hint_text_blob(row: dict[str, Any], bead: dict[str, Any]) -> str:
    parts = [
        str(bead.get("title") or ""),
        " ".join(str(x) for x in (bead.get("summary") or [])),
        str(bead.get("detail") or ""),
        " ".join(str(x) for x in (bead.get("entities") or [])),
        " ".join(str(x) for x in (bead.get("entity_refs") or [])),
        " ".join(str(x) for x in (bead.get("tags") or [])),
        " ".join(str(x) for x in (bead.get("topics") or [])),
        str(bead.get("source_id") or ""),
        str(bead.get("source_ref") or ""),
        str(bead.get("source_system") or ""),
        str(row.get("semantic_text") or ""),
        str(row.get("lexical_text") or ""),
    ]
    return " ".join(x for x in parts if x).lower()


def _relation_family_for_hint(rel: str) -> str:
    return relation_family(rel)


def _apply_hint_boosts(
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    hints_payload: dict[str, Any] | None,
    relation_summary: dict[str, dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hints = normalize_causal_hints(hints_payload)
    if not any([hints.get("bead_types"), hints.get("keywords"), hints.get("entities"), hints.get("causal_labels"), hints.get("relation_families"), hints.get("anchor_ids")]):
        return rows, {"applied": False, "boosted_count": 0, "pinned_count": 0}

    hint_terms = [str(x).lower() for x in list(hints.get("keywords") or []) + list(hints.get("entities") or []) if str(x).strip()]
    hinted_types = set(hints.get("bead_types") or set())
    hinted_rels = set(hints.get("causal_labels") or set())
    hinted_families = set(hints.get("relation_families") or set())
    pinned_ids = [str(x) for x in (hints.get("anchor_ids") or []) if str(x)]

    seen = {str(r.get("bead_id") or "") for r in rows}
    pinned_added = 0
    for bid in pinned_ids:
        if bid in seen or bid not in by_id:
            continue
        rows.append({"bead_id": bid, "score": 1.0, "semantic_score": 1.0, "anchor_reason": "pinned_hint", "source_surface": str((by_id.get(bid) or {}).get("source_surface") or "projection")})
        seen.add(bid)
        pinned_added += 1

    boosted = 0
    for row in rows:
        bid = str(row.get("bead_id") or "")
        bead = (by_id.get(bid) or {}).get("bead") or {}
        if not isinstance(bead, dict):
            continue
        boost = 0.0
        matches: list[str] = []
        bead_type = normalize_bead_type(str(bead.get("type") or ""))
        if bead_type in hinted_types:
            boost += 0.08
            matches.append(f"bead_type:{bead_type}")
        blob = _hint_text_blob(row, bead)
        for term in hint_terms[:12]:
            if term and term in blob:
                boost += 0.025
                matches.append(f"term:{term}")
        rel_counts = relation_summary.get(bid) or {}
        for rel, count in rel_counts.items():
            if str(rel).endswith("_count"):
                continue
            if int(count or 0) <= 0:
                continue
            rel_n = normalize_relation_type(str(rel))
            if rel_n in hinted_rels:
                boost += 0.04
                matches.append(f"relation:{rel_n}")
            family = _relation_family_for_hint(rel_n)
            if family in hinted_families:
                boost += 0.03
                matches.append(f"relation_family:{family}")
        if bid in pinned_ids:
            boost += 0.25
            matches.append("pinned_anchor")
            row["anchor_reason"] = "pinned_hint"
        if boost <= 0:
            continue
        boost = min(0.30, boost)
        row["hint_boost"] = round(boost, 6)
        row["hint_matches"] = sorted(set(matches))[:12]
        row["score"] = float(row.get("score") or 0.0) + boost
        row["context_bias_score"] = float(row.get("context_bias_score") or 0.0) + min(0.12, boost)
        boosted += 1

    return rows, {
        "applied": True,
        "boosted_count": boosted,
        "pinned_count": pinned_added,
        "hint_terms": hint_terms[:12],
        "bead_types": sorted(hinted_types),
        "relation_families": sorted(hinted_families),
        "causal_labels": sorted(hinted_rels),
    }


def search_request(
    *,
    root: str | Path,
    query: str,
    k: int = 10,
    intent: str = "remember",
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rp = Path(root)
    _caps = get_backend_capabilities(rp / ".beads")
    sub_preview = dict(submission or {})
    # Current-truth guard: superseded versions enter the corpus only when a
    # provenance caller opts in explicitly.
    include_superseded = bool(sub_preview.get("include_superseded"))
    corpus = build_visible_corpus(rp, include_superseded=include_superseded)
    catalog = build_catalog(rp)
    entity_registry = load_entity_registry(rp)
    entity_context = infer_query_entity_context(query, entity_registry)
    expanded_query = expand_query_with_entities(query, entity_context, entity_registry)
    sub = dict(submission or {})
    as_of_raw = str(sub.get("as_of") or "").strip() or None
    tr = dict(sub.get("time_range") or {})
    if not as_of_raw:
        as_of_raw = str(tr.get("to") or "").strip() or None

    claim_state, claim_warnings = _load_claim_state(rp, as_of=as_of_raw)
    retrieval_mode = plan_retrieval_mode(query, catalog, claim_state)
    by_id = {str(r.get("bead_id") or ""): r for r in corpus}
    projection_created_at: dict[str, str] = {}
    relation_summary: dict[str, dict[str, int]] = {}
    retrieval_value_bonus: dict[str, float] = {}
    myelination_bonus: dict[str, float] = {}
    try:
        idx = json.loads((rp / ".beads" / "index.json").read_text(encoding="utf-8"))
        for bid, bead in ((idx.get("beads") or {}) if isinstance(idx, dict) else {}).items():
            if isinstance(bead, dict):
                projection_created_at[str(bid)] = str(bead.get("created_at") or "")
        relation_summary = _relation_summary_from_index(idx if isinstance(idx, dict) else {})
        retrieval_value_bonus = _retrieval_value_bonus_from_index(idx if isinstance(idx, dict) else {})
    except Exception:
        projection_created_at = {}
        relation_summary = {}
        retrieval_value_bonus = {}

    try:
        if myelination_enabled():
            myelination_bonus = read_myelination_bead_bonus_map(rp)
    except Exception:
        myelination_bonus = {}

    if relation_summary:
        for bid, rel in relation_summary.items():
            row = by_id.get(str(bid))
            if not isinstance(row, dict):
                continue
            bead = dict((row.get("bead") or {}))
            bead["supersedes_count"] = int(rel.get("supersedes_count") or 0)
            bead["superseded_by_count"] = int(rel.get("superseded_by_count") or 0)
            bead["contradicts_count"] = int(rel.get("contradicts_count") or 0)
            row["bead"] = bead
            by_id[str(bid)] = row

    if retrieval_value_bonus:
        for bid, bonus in retrieval_value_bonus.items():
            row = by_id.get(str(bid))
            if not isinstance(row, dict):
                continue
            bead = dict((row.get("bead") or {}))
            bead["retrieval_value_bonus"] = float(bonus)
            row["bead"] = bead
            by_id[str(bid)] = row

    if myelination_bonus:
        for bid, bonus in myelination_bonus.items():
            row = by_id.get(str(bid))
            if not isinstance(row, dict):
                continue
            bead = dict((row.get("bead") or {}))
            bead["myelination_bonus"] = float(bonus)
            row["bead"] = bead
            by_id[str(bid)] = row

    sem_k = max(24, int(k) * 2)
    if retrieval_mode == "fact_first":
        sem_k = max(32, int(k) * 3)
    elif retrieval_mode == "causal_first":
        sem_k = max(24, int(k) * 2)
    elif retrieval_mode == "temporal_first":
        sem_k = max(20, int(k) * 2)

    try:
        if _caps.vector_search:
            # Qdrant hybrid: sparse+dense in one query with eligibility filters pushed down
            sem = hybrid_lookup(rp, expanded_query or query, k=sem_k)
        else:
            sem = semantic_lookup(rp, expanded_query or query, k=sem_k, mode=_canonical_semantic_mode())
    except RuntimeError:
        sem = {"ok": False, "warnings": ["semantic_backend_unavailable"]}
    if not sem.get("ok"):
        return _semantic_failure_response(
            query=query,
            intent=intent,
            k=int(k),
            warnings=list(sem.get("warnings") or []) + list(claim_warnings),
            provider=str(sem.get("provider") or ""),
        )
    sem_rows = [dict(r or {}) for r in (sem.get("results") or []) if str((r or {}).get("bead_id") or "") in by_id]
    conv = run_hybrid_rerank_seeds(
        rp,
        query=expanded_query or query,
        intent=intent,
        k=max(12, int(k) * 2),
    )
    conv_by_id = dict(conv.get("by_id") or {}) if bool(conv.get("ok")) else {}

    # Convergence: enrich semantic candidates with hybrid/rerank strength.
    for r in sem_rows:
        bid = str(r.get("bead_id") or "")
        crow = dict(conv_by_id.get(bid) or {})
        if not crow:
            continue
        r["fused_score"] = float(crow.get("fused_score") or 0.0)
        r["rerank_seed_score"] = float(crow.get("rerank_score") or 0.0)
        r["sem_score"] = float(crow.get("sem_score") or 0.0)
        r["lex_score"] = float(crow.get("lex_score") or 0.0)
        feats = dict(crow.get("features") or {})
        # Structural lift as soft bias.
        r["context_bias_score"] = max(float(r.get("context_bias_score") or 0.0), float(feats.get("structural_add") or 0.0))

    # Add strong hybrid/rerank candidates missing from semantic seed set.
    if conv_by_id:
        seen_sem = {str(r.get("bead_id") or "") for r in sem_rows}
        add_cap = max(2, int(k))
        added = 0
        for crow in (conv.get("results") or []):
            bid = str(crow.get("bead_id") or "")
            if not bid or bid in seen_sem:
                continue
            if bid not in by_id:
                continue
            if added >= add_cap:
                break
            sem_rows.append(
                {
                    "bead_id": bid,
                    "score": float(crow.get("rerank_score") or crow.get("fused_score") or 0.0),
                    "semantic_score": float(crow.get("sem_score") or 0.0),
                    "fused_score": float(crow.get("fused_score") or 0.0),
                    "rerank_seed_score": float(crow.get("rerank_score") or 0.0),
                    "sem_score": float(crow.get("sem_score") or 0.0),
                    "lex_score": float(crow.get("lex_score") or 0.0),
                    "anchor_reason": "hybrid_rerank_seed",
                    "source_surface": str((by_id.get(bid) or {}).get("source_surface") or "projection"),
                    "context_bias_score": float((crow.get("features") or {}).get("structural_add") or 0.0),
                }
            )
            seen_sem.add(bid)
            added += 1

    claim_rows: list[dict[str, Any]] = []
    if claim_state and _claim_query_signal(query, retrieval_mode):
        claim_limit = max(1, min(3, int(k))) if retrieval_mode == "fact_first" else max(1, min(2, int(k)))
        claim_rows = _claim_anchors_from_state(
            query=query,
            claim_state=claim_state,
            by_id=by_id,
            limit=claim_limit,
        )
        if claim_rows:
            seen_ids = {str(r.get("bead_id") or "") for r in sem_rows}
            sem_rows = claim_rows + [r for r in sem_rows if str(r.get("bead_id") or "") not in seen_ids]
    continuity_query = _is_continuity_query(query)
    for r in sem_rows:
        r.setdefault("anchor_reason", "retrieved")
        r.setdefault("source_surface", (by_id.get(str(r.get("bead_id") or ""), {}) or {}).get("source_surface", "projection"))
        bead = ((by_id.get(str(r.get("bead_id") or ""), {}) or {}).get("bead") or {})
        bead_type = str(bead.get("type") or "").strip().lower()
        adjust = _session_start_score_adjustment(bead_type=bead_type, continuity_query=continuity_query)
        effective = max(0.0, float(r.get("score") or 0.0) + adjust)
        r["_bead_type"] = bead_type
        r["_type_priority_rank"] = _type_priority_rank(bead_type, continuity_query=continuity_query)
        r["score"] = effective
        if bead_type == "session_start":
            r["anchor_reason"] = "continuity_snapshot" if continuity_query else "continuity_snapshot_demoted"

    strong_sem = [r for r in sem_rows if float(r.get("score") or 0.0) >= 0.55 and r.get("anchor_reason") == "retrieved"]
    if len(strong_sem) < 3:
        rescue = _lexical_rescue(query, corpus, max_add=2)
        seen = {str(r.get("bead_id") or "") for r in sem_rows}
        for rr in rescue:
            if rr["bead_id"] not in seen:
                sem_rows.append(rr)

    sem_rows, filter_warnings = _apply_typed_filters(sem_rows, by_id, projection_created_at, submission)
    hint_diag: dict[str, Any] = {"applied": False, "boosted_count": 0, "pinned_count": 0}
    sem_rows, hint_diag = _apply_hint_boosts(sem_rows, by_id, sub.get("hints") or ((sub.get("facets") or {}).get("hints") if isinstance(sub.get("facets"), dict) else None), relation_summary)

    if claim_retrieval_boost_enabled() and claim_state:
        sem_rows = boost_claim_results(sem_rows, claim_state)

    sem_rows = rerank_semantic_rows(
        rows=sem_rows,
        by_id=by_id,
        query=expanded_query or query,
        retrieval_mode=retrieval_mode,
        claim_state=claim_state,
        as_of=as_of_raw,
        entity_context=entity_context,
    )

    sem_rows.sort(
        key=lambda r: (
            0 if str(r.get("anchor_reason") or "") == "pinned" else (1 if str(r.get("anchor_reason") or "") == "strict_facet_match" else 2),
            -float(r.get("rank_score") or r.get("score") or 0.0),
            -float(r.get("semantic_score") if r.get("semantic_score") is not None else (r.get("score") or 0.0)),
            (int(r.get("_type_priority_rank")) if r.get("_type_priority_rank") is not None else 9),
            _status_rank(str(r.get("status") or "")),
            str((by_id.get(str(r.get("bead_id") or ""), {}) or {}).get("created_at") or ""),
            str(r.get("bead_id") or ""),
        )
    )

    anchors = [_to_anchor(r, by_id) for r in sem_rows[: max(1, int(k))]]
    confidence = "high" if anchors and float(anchors[0].get("semantic_score") or 0.0) >= 0.75 else ("medium" if anchors else "low")
    next_action = "answer" if confidence in {"high", "medium"} else "ask_clarifying"

    # stale-budget guard
    max_stale_ms = int(os.getenv("CORE_MEMORY_SEMANTIC_MAX_STALE_MS", "120000") or "120000")
    stale_age_ms = sem.get("stale_age_ms")
    strong = [a for a in anchors if float(a.get("semantic_score") or 0.0) >= 0.55 and str(a.get("anchor_reason") or "") == "retrieved"]
    weak_anchors = (not anchors) or (float((anchors[0] or {}).get("semantic_score") or 0.0) < 0.65) or (len(strong) < 2)
    if isinstance(stale_age_ms, int) and stale_age_ms > max_stale_ms:
        warns = list(sem.get("warnings") or [])
        if "semantic_index_over_stale_budget" not in warns:
            warns.append("semantic_index_over_stale_budget")
        sem["warnings"] = warns
        if weak_anchors:
            confidence = "low"
            if intent == "causal":
                next_action = "ask_clarifying"

    return {
        "ok": True,
        "degraded": bool(sem.get("degraded", False)),
        "anchors": anchors,
        "results": anchors,  # compatibility alias
        "chains": [],
        "citations": [],
        "confidence": confidence,
        "next_action": next_action,
        "warnings": list(sem.get("warnings") or [])
        + list(filter_warnings)
        + list(claim_warnings)
        + (["hybrid_seed_unavailable"] if not bool(conv.get("ok")) else []),
        "retrieval_mode": retrieval_mode,
        "retrieval_stages": {
            "semantic_seed_count": int(len(sem.get("results") or [])),
            "hybrid_seed_count": int(((conv.get("stages") or {}).get("hybrid_candidates") or 0)),
            "hybrid_rerank_count": int(((conv.get("stages") or {}).get("rerank_candidates") or 0)),
            "post_filter_count": int(len(sem_rows)),
            "hint_boosts": hint_diag,
        },
        "snapped": {
            "raw_query": query,
            "intent": intent,
            "k": int(k),
            **{
                key: value
                for key, value in dict(submission or {}).items()
                if key in {"incident_id", "scope", "topic_keys", "bead_types", "relation_types", "must_terms", "avoid_terms", "time_range", "require_structural", "hints"}
            },
        },
        "claim_context": {
            "enabled": bool(claim_layer_enabled()),
            "resolved": bool(claim_state),
            "active_slots": int((claim_state or {}).get("active_slots") or 0),
            "total_slots": int((claim_state or {}).get("total_slots") or 0),
            "as_of": as_of_raw,
            "claim_anchor_count": int(len([r for r in sem_rows[: max(1, int(k))] if str(r.get("anchor_reason") or "") == "claim_current_state"])),
        },
        "entity_context": {
            "resolved_entity_ids": list(entity_context.get("resolved_entity_ids") or []),
            "matched_aliases": list(entity_context.get("matched_aliases") or []),
            "labels": list(entity_context.get("labels") or []),
            "expanded_query": expanded_query,
        },
    }


def trace_request(
    *,
    root: str | Path,
    query: str = "",
    anchor_ids: list[str] | None = None,
    k: int = 10,
    intent: str = "causal",
    hydration: dict[str, Any] | None = None,
    submission: dict[str, Any] | None = None,
    max_depth: int | None = None,
    max_chains: int | None = None,
) -> dict[str, Any]:
    _caps = get_backend_capabilities(Path(root) / ".beads")
    anchors_out: dict[str, Any]
    if anchor_ids:
        corpus = build_visible_corpus(Path(root))
        by_id = {str(r.get("bead_id") or ""): r for r in corpus}
        anchors = []
        for bid in [str(x) for x in (anchor_ids or []) if str(x).strip()]:
            r = {"bead_id": bid, "score": 1.0, "anchor_reason": "pinned", "status": str((by_id.get(bid) or {}).get("status") or "")}
            anchors.append(_to_anchor(r, by_id))
        anchors_out = {"ok": True, "anchors": anchors, "results": anchors, "warnings": [], "confidence": "medium", "next_action": "answer", "snapped": {"raw_query": query, "intent": intent, "k": int(k)}}
    else:
        anchors_out = search_request(root=root, query=query, k=k, intent=intent, submission=submission)

    if not anchors_out.get("ok"):
        return _semantic_failure_response(
            query=query,
            intent=intent,
            k=int(k),
            warnings=list(anchors_out.get("warnings") or []),
            provider=str(anchors_out.get("provider") or ""),
        )

    anchors = anchors_out.get("anchors") or []
    # Seed traversal from the top-N semantic anchors (configurable). The prior
    # hardcoded top-5 starved traversal whenever the gold-adjacent bead ranked
    # just outside the top 5 — common on conversational corpora where the answer
    # lives a turn or two away from the best lexical/semantic match.
    _seed_n = _trace_seed_anchors()
    requested_max_depth = max_depth if max_depth is not None else (submission or {}).get("max_depth")
    try:
        _max_depth = int(requested_max_depth) if requested_max_depth is not None else _trace_max_depth()
    except (TypeError, ValueError):
        _max_depth = _trace_max_depth()
    _max_depth = max(1, _max_depth)
    requested_max_chains = max_chains if max_chains is not None else (submission or {}).get("max_chains")
    try:
        _max_chains = int(requested_max_chains) if requested_max_chains is not None else _trace_max_chains()
    except (TypeError, ValueError):
        _max_chains = _trace_max_chains()
    _max_chains = max(1, _max_chains)
    a_ids = [str(a.get("bead_id") or "") for a in anchors[:_seed_n] if str(a.get("bead_id") or "")]
    # Augment traversal seeds with entity-resolved beads. Semantic search misses
    # can leave the gold-adjacent bead outside the top-N anchors; entity-matched
    # beads provide an independent entry point into the causal graph without
    # depending on embedding similarity.
    if query:
        try:
            from core_memory.entity.retrieval import entity_seed_bead_ids as _entity_seeds
            _idx = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
            _seeds = _entity_seeds(
                query,
                load_entity_registry(str(root)),
                dict(_idx.get("beads") or {}),
                exclude=set(a_ids),
                limit=6,
            )
            a_ids = a_ids + [_bid for _, _bid in _seeds]
        except Exception:
            pass
    def _python_traverse() -> dict[str, Any]:
        return causal_traverse(Path(root), anchor_ids=a_ids, max_depth=_max_depth, max_chains=_max_chains) if a_ids else {"ok": True, "chains": []}

    trav: dict[str, Any] | None = None
    if _caps.graph_traversal:
        _graph = create_graph_backend(Path(root))
        # The backend's own capability declaration is authoritative: the env
        # var says a provider is configured, but only the backend knows
        # whether its traverse() produces canonical bead chains (graphiti/zep
        # return fact search hits and must not serve the trace contract).
        _backend_can_traverse = False
        if not isinstance(_graph, NullGraphBackend):
            try:
                _backend_can_traverse = bool(_graph.capabilities().graph_traversal)
            except Exception:
                _backend_can_traverse = False
        if _backend_can_traverse:
            _raw_chains = _graph.traverse(seed_ids=a_ids, edge_types=None, max_hops=_max_depth, max_chains=_max_chains)
            # Active-association view first: the canonical index owns association
            # status and the backend can lag it (retraction edits index.json
            # without a backend resync), so truncate chains at the first edge
            # with no active association before scoring.
            from core_memory.graph.traversal import filter_chains_to_active_edges as _active_filter
            _raw_chains = _active_filter(Path(root), list(_raw_chains or []))
            if _raw_chains or not a_ids:
                # Normalise backend chains to the canonical format expected by
                # trace_request's downstream consumers: adds "path" (ordered bead
                # IDs from "nodes"), normalises "tgt"→"dst", and computes a
                # "score" using the shared edge-weight table so all backends rank
                # consistently.
                from core_memory.graph.edge_weights import normalize_backend_chain as _norm_chain
                _chains = [_norm_chain(c) for c in _raw_chains]
                trav = {"ok": True, "chains": _chains, "backend": _graph.name}
            # else: every backend chain was filtered/empty while seeds exist —
            # fall through to the Python walk over the canonical index, which
            # is never worse than an empty backend result.
    if trav is None:
        trav = _python_traverse()
    chains = list(trav.get("chains") or [])
    relation_filter = {normalize_relation_type(str(x).strip()) for x in ((submission or {}).get("relation_types") or []) if str(x).strip()}
    if relation_filter:
        # Hard filter: caller explicitly constrained relation types.
        chains = [
            c
            for c in chains
            if {normalize_relation_type(str((e or {}).get("rel") or "").strip()) for e in (c.get("edges") or [])}.intersection(relation_filter)
        ]
    else:
        # Soft structural hints (e.g. parsed from the query): prefer chains whose
        # edges match the hinted relations by sorting them first, but never drop
        # the others — a wrong parse must not destroy recall.
        hint_rels = {normalize_relation_type(str(x).strip()) for x in ((submission or {}).get("structural_hint_relations") or []) if str(x).strip()}
        if hint_rels and chains:
            def _chain_hint_rank(c: dict[str, Any]) -> tuple[int, float]:
                edge_rels = {normalize_relation_type(str((e or {}).get("rel") or "").strip()) for e in (c.get("edges") or [])}
                matched = bool(edge_rels.intersection(hint_rels))
                return (0 if matched else 1, -float(c.get("score") or 0.0))
            chains = sorted(chains, key=_chain_hint_rank)

    # Grounding levels:
    # - full: chains include at least one non-temporal structural relation
    # - partial: chains exist but are temporal/non-structural only, OR semantic anchor set contains
    #            decision/precedent + supporting role(s)
    # - none: no usable grounding
    has_non_temporal_structural = _has_non_temporal_structural_edge(chains)
    if chains and has_non_temporal_structural:
        grounding = "full"
    elif chains:
        grounding = "partial"
    else:
        types = {str(a.get("type") or "").lower() for a in anchors}
        has_decision_like = bool(types.intersection({"decision", "precedent"}))
        has_support_like = bool(types.intersection({"evidence", "lesson", "outcome"}))
        grounding = "partial" if (anchors and has_decision_like and has_support_like) else "none"

    anchor_types = {str(a.get("type") or "").strip().lower() for a in anchors if str(a.get("type") or "").strip()}
    if grounding == "full" and anchor_types and anchor_types.issubset({"session_start"}):
        grounding = "partial"

    next_action = "answer" if grounding in {"full", "partial"} else "ask_clarifying"
    confidence = "high" if grounding == "full" else ("medium" if grounding == "partial" else ("medium" if anchors else "low"))

    citations = []
    if chains:
        for c in chains[:3]:
            for b in (c.get("beads") or []):
                bid = str((b or {}).get("id") or "")
                if bid and bid not in {x.get("bead_id") for x in citations}:
                    citations.append({"bead_id": bid, "title": str((b or {}).get("title") or ""), "type": str((b or {}).get("type") or "")})
    elif grounding == "partial":
        # Semantic-only partial grounding cites top anchors.
        for a in anchors[:5]:
            bid = str(a.get("bead_id") or "")
            if bid and bid not in {x.get("bead_id") for x in citations}:
                citations.append({"bead_id": bid, "title": str(a.get("title") or ""), "type": str(a.get("type") or "")})

    results = list(anchors)
    seen_result_ids = {str(a.get("bead_id") or "") for a in results if str(a.get("bead_id") or "")}
    if chains:
        # Give chain beads a merge budget BEYOND k so traversal evidence is not
        # crowded out by the k semantic anchors. Previously the cap was a flat k,
        # so with k semantic anchors already filling the budget, traversal added
        # ~nothing to the scored top-k. The merged result list is over-filled
        # here; downstream callers still score/trim to their own k.
        _merge_cap = max(1, int(k)) + _trace_chain_merge_bonus()
        corpus = build_visible_corpus(Path(root))
        by_id = {str(r.get("bead_id") or ""): r for r in corpus}
        for chain in chains:
            chain_score = float(chain.get("score") or 0.0)
            for idx, bid in enumerate([str(x or "").strip() for x in (chain.get("path") or []) if str(x or "").strip()]):
                if not bid or bid in seen_result_ids:
                    continue
                if bid not in by_id:
                    continue
                results.append(
                    _to_anchor(
                        {
                            "bead_id": bid,
                            "score": max(0.0, chain_score - (0.01 * idx)),
                            "anchor_reason": "trace_chain",
                            "source_surface": "causal_trace",
                        },
                        by_id,
                    )
                )
                seen_result_ids.add(bid)
                if len(results) >= _merge_cap:
                    break
            if len(results) >= _merge_cap:
                break

    out = {
        "ok": True,
        "degraded": bool(anchors_out.get("degraded", False)),
        "anchors": anchors,
        "results": results,
        "chains": chains,
        "citations": citations,
        "grounding": {
            "required": True,
            "achieved": bool(grounding in {"full", "partial"}),
            "level": grounding,
            "reason": (
                "grounded"
                if grounding == "full"
                else (
                    "non_temporal_structural_missing"
                    if chains and grounding == "partial"
                    else ("semantic_only" if grounding == "partial" else "none")
                )
            ),
        },
        "confidence": confidence,
        "next_action": next_action,
        "warnings": list(anchors_out.get("warnings") or []),
        "snapped": anchors_out.get("snapped") or {"raw_query": query, "intent": intent, "k": int(k)},
        "hydration": {"status": "not_requested", "warnings": []},
        "trace_diagnostics": {
            **dict(trav.get("assoc_diag") or {}),
            "seed_anchor_count": int(len(a_ids)),
            "requested_max_depth": None if requested_max_depth is None else str(requested_max_depth),
            "max_depth": int(_max_depth),
            "requested_max_chains": None if requested_max_chains is None else str(requested_max_chains),
            "max_chains": int(_max_chains),
            "backend": str(trav.get("backend") or ("python" if a_ids else "none")),
        },
    }

    hyd_req = dict(hydration or {})
    if hyd_req:
        status = "complete"
        hcfg, hw = _normalize_public_hydration_request(hyd_req)
        try:
            bead_ids = [str(a.get("bead_id") or "") for a in (out.get("anchors") or []) if str(a.get("bead_id") or "")]
            h = hydrate_bead_sources_for_root(
                root=str(root),
                bead_ids=bead_ids[: int(hcfg.get("max_beads") or 10)],
                include_tools=True,
                before=int(hcfg.get("adjacent_before") or 0),
                after=int(hcfg.get("adjacent_after") or 0),
            )
            out["hydration_data"] = h
            if h.get("disabled"):
                status = "partial"
                hw.append("hydration_disabled")
        except Exception:
            status = "failed"
            hw.append("hydration_error")
        out["hydration"] = {"status": status, "warnings": hw, "request": hcfg}

    return out


def execute_request(*, root: str | Path, request: dict[str, Any], explain: bool = True) -> dict[str, Any]:
    rp = Path(root)
    req = dict(request or {})
    query = str(req.get("raw_query") or req.get("query_text") or req.get("query") or "").strip()
    declared_intent = str(req.get("intent") or "").strip()
    intent = declared_intent or str((classify_intent(query) or {}).get("intent_class") or "remember")
    grounding_mode = str(req.get("grounding_mode") or "").strip()
    constraints = dict(req.get("constraints") or {})
    if not grounding_mode and bool(constraints.get("require_structural")):
        grounding_mode = "require_grounded"
    if not grounding_mode:
        grounding_mode = "prefer_grounded" if intent == "causal" else "search_only"

    k = int(req.get("k") or 10)
    if grounding_mode == "search_only":
        facets = dict(req.get("facets") or {})
        as_of = str(req.get("as_of") or facets.get("as_of") or "").strip() or None
        submission = {
            "query_text": query,
            "intent": intent,
            "k": k,
            "as_of": as_of,
            "incident_id": str((facets.get("incident_ids") or [None])[0] or "").strip() or None,
            "scope": str(facets.get("scope") or "").strip() or None,
            "topic_keys": list(facets.get("topic_keys") or []),
            "bead_types": list(facets.get("bead_types") or []),
            "relation_types": list(facets.get("relation_types") or []),
            "structural_hint_relations": list(facets.get("structural_hint_relations") or []),
            "must_terms": list(facets.get("must_terms") or []),
            "avoid_terms": list(facets.get("avoid_terms") or []),
            "time_range": dict(facets.get("time_range") or {}),
            "metadata": {**dict(facets.get("metadata") or {}), **_metadata_constraints(constraints)},
            "require_structural": bool(constraints.get("require_structural", False)),
            "hints": dict(req.get("hints") or facets.get("hints") or {}),
        }
        out = search_request(root=rp, query=query, k=k, intent=intent, submission=submission)
        if not out.get("ok"):
            out.setdefault("request", {
                "raw_query": query,
                "intent": intent,
                "k": k,
                "grounding_mode": grounding_mode,
                "constraints": {"require_structural": bool(constraints.get("require_structural", False))},
                "facets": dict(req.get("facets") or {}),
            })
            out.setdefault("contract", "memory_execute")
            out.setdefault("schema_version", "memory_execute_result.v1")
            out.setdefault("next_action", "ask_clarifying")
            out.setdefault("suggested_next", out.get("next_action"))
            return out
        out["grounding"] = {"required": False, "achieved": False, "level": "none", "reason": "search_only"}
        out.setdefault("hydration", {"status": "not_requested", "warnings": []})
    else:
        facets = dict(req.get("facets") or {})
        as_of = str(req.get("as_of") or facets.get("as_of") or "").strip() or None
        submission = {
            "query_text": query,
            "intent": intent,
            "k": k,
            "as_of": as_of,
            "incident_id": str((facets.get("incident_ids") or [None])[0] or "").strip() or None,
            "scope": str(facets.get("scope") or "").strip() or None,
            "topic_keys": list(facets.get("topic_keys") or []),
            "bead_types": list(facets.get("bead_types") or []),
            "relation_types": list(facets.get("relation_types") or []),
            "structural_hint_relations": list(facets.get("structural_hint_relations") or []),
            "must_terms": list(facets.get("must_terms") or []),
            "avoid_terms": list(facets.get("avoid_terms") or []),
            "time_range": dict(facets.get("time_range") or {}),
            "metadata": {**dict(facets.get("metadata") or {}), **_metadata_constraints(constraints)},
            "require_structural": bool(constraints.get("require_structural", False)),
            "hints": dict(req.get("hints") or facets.get("hints") or {}),
        }
        out = trace_request(
            root=rp,
            query=query,
            anchor_ids=req.get("anchor_ids") or None,
            k=k,
            intent=intent,
            hydration=req.get("hydration") or None,
            submission=submission,
            max_depth=req.get("max_depth") or req.get("trace_max_depth") or None,
            max_chains=req.get("max_chains") or req.get("trace_max_chains") or None,
        )
        if not out.get("ok"):
            out.setdefault("request", {
                "raw_query": query,
                "intent": intent,
                "k": k,
                "grounding_mode": grounding_mode,
                "constraints": {"require_structural": bool(constraints.get("require_structural", False))},
                "facets": dict(req.get("facets") or {}),
            })
            out.setdefault("contract", "memory_execute")
            out.setdefault("schema_version", "memory_execute_result.v1")
            out.setdefault("next_action", "ask_clarifying")
            out.setdefault("suggested_next", out.get("next_action"))
            return out

    out.setdefault("chains", [])
    out.setdefault("citations", [])

    # explicit best-effort hydration (post-selection, non-fatal) for search_only path
    hyd_req = dict(req.get("hydration") or {})
    if hyd_req and grounding_mode == "search_only":
        status = "complete"
        hcfg, hw = _normalize_public_hydration_request(hyd_req)
        try:
            bead_ids = [str(a.get("bead_id") or "") for a in (out.get("anchors") or []) if str(a.get("bead_id") or "")]
            h = hydrate_bead_sources_for_root(
                root=str(root),
                bead_ids=bead_ids[: int(hcfg.get("max_beads") or 10)],
                include_tools=True,
                before=int(hcfg.get("adjacent_before") or 0),
                after=int(hcfg.get("adjacent_after") or 0),
            )
            out["hydration_data"] = h
            if h.get("disabled"):
                status = "partial"
                hw.append("hydration_disabled")
        except Exception:
            status = "failed"
            hw.append("hydration_error")
        out["hydration"] = {"status": status, "warnings": hw, "request": hcfg}

    out["request"] = {
        "raw_query": query,
        "intent": intent,
        "k": k,
        "as_of": str(req.get("as_of") or "").strip() or None,
        "grounding_mode": grounding_mode,
        "constraints": {"require_structural": bool(constraints.get("require_structural", False))},
        "facets": dict(req.get("facets") or {}),
    }
    out.setdefault("contract", "memory_execute")
    out.setdefault("schema_version", "memory_execute_result.v1")
    out["suggested_next"] = out.get("next_action")

    first = (out.get("results") or [{}])[0] if (out.get("results") or []) else {}
    first_surface = str((first or {}).get("source_surface") or "")
    if first_surface in {"session", "projection"}:
        first_surface = "session_bead"
    out.setdefault("source_surface", first_surface or "session_bead")
    out.setdefault("source_scope", "durable")
    out.setdefault("source_priority_applied", ["session_bead", "archive_graph", "rolling_window", "transcript", "memory_md"])

    if claim_layer_enabled():
        as_of = str(req.get("as_of") or "").strip() or None
        if not as_of:
            tr = dict(req.get("time_range") or {})
            as_of = str(tr.get("to") or "").strip() or None
        claim_state, claim_warnings = _load_claim_state(rp, as_of=as_of)
        policy = score_answer(list(out.get("results") or []), claim_state, query, as_of=as_of)
        out["answer_policy"] = policy
        out["answer_outcome"] = str(policy.get("outcome") or "answer_partial")
        candidate = _claim_answer_candidate(
            query=query,
            claim_state=claim_state,
            results=list(out.get("results") or []),
            answer_outcome=out["answer_outcome"],
            as_of=as_of,
        )
        if candidate:
            out["answer_candidate"] = candidate
            cites = list(out.get("citations") or [])
            top = (out.get("results") or [{}])[0] if (out.get("results") or []) else {}
            bid = str((top or {}).get("bead_id") or "")
            if bid and not any(str((c or {}).get("bead_id") or "") == bid for c in cites if isinstance(c, dict)):
                cites.append({
                    "bead_id": bid,
                    "reason": "claim_state_current_slot",
                    "slot_key": str(candidate.get("slot_key") or ""),
                    "claim_id": str(candidate.get("claim_id") or ""),
                })
            out["citations"] = cites
        if out["answer_outcome"] == "abstain":
            out["next_action"] = "ask_clarifying"
            out["suggested_next"] = "ask_clarifying"
        if claim_warnings:
            warns = list(out.get("warnings") or [])
            warns.extend([w for w in claim_warnings if w not in warns])
            out["warnings"] = warns

    if explain:
        out["explain"] = {"planner": "canonical_v9", "stages": ["normalize", "anchors", "trace_or_not", "finalize"]}
    return out
