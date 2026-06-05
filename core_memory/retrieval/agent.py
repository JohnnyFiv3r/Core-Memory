from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.retrieval.contracts import (
    ClaimSlotItem,
    ConflictItem,
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    ResolvedGoalItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)
from core_memory.persistence.store_claim_ops import read_all_claim_rows, resolve_current_state
from core_memory.retrieval.causal_recall import attach_causal_recall_pipeline, normalize_recall_hints, should_run_causal_pipeline
from core_memory.retrieval.tools.memory import execute as memory_execute

_CAUSAL_HINTS = {
    "why",
    "how",
    "cause",
    "caused",
    "because",
    "decision",
    "decide",
    "decided",
    "rationale",
    "reason",
    "led",
    "lead",
    "blocked",
    "unblocked",
    "changed",
    "supersede",
    "superseded",
    "resolved",
    "outcome",
    "last week",
    "timeline",
    "history",
}

_EFFORT_DEFAULTS: dict[str, dict[str, Any]] = {
    "low": {
        "k": 8,
        "grounding_mode": "search_only",
        "association_hops": 0,
        "hydration": {},
        "planning_reason": "low effort uses direct lookup for low-latency recall",
    },
    "medium": {
        "k": 12,
        "grounding_mode": "prefer_grounded",
        "association_hops": 1,
        "hydration": {"turn_sources": True, "max_beads": 8, "adjacent_before": 1, "adjacent_after": 1},
        "planning_reason": "medium effort: vector top-k + 1-hop association expansion",
    },
    "high": {
        "k": 20,
        "grounding_mode": "prefer_grounded",
        "association_hops": 2,
        "hydration": {"turn_sources": True, "max_beads": 16, "adjacent_before": 2, "adjacent_after": 2},
        "planning_reason": "high effort: vector top-k + 2-hop association/claim graph expansion for multi-hop evidence",
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _query_text(request: dict[str, Any]) -> str:
    return _text(request.get("raw_query") or request.get("query_text") or request.get("query"))


def _looks_causal_or_temporal(query: str) -> bool:
    q = f" {query.lower()} "
    return any(hint in q for hint in _CAUSAL_HINTS)


def _read_index(root: str) -> dict[str, Any]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_update_by_bead(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for bead_id, bead in (index.get("beads") or {}).items():
        if not isinstance(bead, dict):
            continue
        updates = [u for u in (bead.get("claim_updates") or []) if isinstance(u, dict)]
        if not updates:
            continue
        updates.sort(
            key=lambda u: (
                int(u.get("chain_seq") or 0) if str(u.get("chain_seq") or "").isdigit() else 0,
                str(u.get("id") or ""),
            )
        )
        out[str(bead_id)] = updates[-1]
    return out


def _add_evidence_grounding(result: RecallResult, index: dict[str, Any]) -> None:
    latest = _latest_update_by_bead(index)
    for item in result.evidence:
        if item.grounding_hash:
            continue
        update = latest.get(str(item.bead_id)) or {}
        grounding_hash = _text(update.get("grounding_hash"))
        if grounding_hash:
            item.grounding_hash = grounding_hash


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(value or "").lower()) if t}


def _resolved_goals_for_result(result: RecallResult, index: dict[str, Any], query: str) -> list[ResolvedGoalItem]:
    beads = index.get("beads") or {}
    evidence_ids = {str(e.bead_id) for e in result.evidence if str(e.bead_id)}
    associated_ids: set[str] = set(evidence_ids)
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        src = _text(assoc.get("source_bead") or assoc.get("source_bead_id"))
        tgt = _text(assoc.get("target_bead") or assoc.get("target_bead_id"))
        if src in evidence_ids and tgt:
            associated_ids.add(tgt)
        if tgt in evidence_ids and src:
            associated_ids.add(src)
    q_tokens = _tokens(query)
    evidence_tokens: set[str] = set()
    for bid in evidence_ids:
        row = beads.get(bid) if isinstance(beads, dict) else None
        if isinstance(row, dict):
            evidence_tokens |= _tokens(" ".join([str(row.get("title") or ""), " ".join(str(x) for x in (row.get("summary") or []))]))

    items: list[ResolvedGoalItem] = []
    for bid, row in beads.items() if isinstance(beads, dict) else []:
        if not isinstance(row, dict):
            continue
        if _text(row.get("type")).lower() != "goal":
            continue
        state = _text(row.get("promotion_state") or row.get("goal_status") or row.get("status")).lower()
        if state != "resolved":
            continue
        resolved_by = _text(row.get("resolved_by_bead_id"))
        text_tokens = _tokens(" ".join([str(row.get("title") or ""), " ".join(str(x) for x in (row.get("summary") or []))]))
        related = (
            str(bid) in associated_ids
            or resolved_by in evidence_ids
            or bool(text_tokens & q_tokens)
            or bool(text_tokens & evidence_tokens)
        )
        if not related:
            continue
        items.append(
            ResolvedGoalItem(
                bead_id=str(bid),
                title=_text(row.get("title")),
                resolved_by_bead_id=resolved_by,
                resolved_at=_text(row.get("resolved_at")),
                reason=_text(row.get("promotion_reason")),
                metadata={"status": state},
            )
        )
    items.sort(key=lambda g: (g.resolved_at, g.bead_id), reverse=True)
    return items


def _latest_slot_update(updates: list[dict[str, Any]], subject: str, slot: str) -> dict[str, Any]:
    matching = [u for u in updates if _text(u.get("subject")) == subject and _text(u.get("slot")) == slot]
    if not matching:
        return {}
    matching.sort(
        key=lambda u: (
            int(u.get("chain_seq") or 0) if str(u.get("chain_seq") or "").isdigit() else 0,
            _text(u.get("id")),
        )
    )
    return matching[-1]


def _claim_slots_for_result(result: RecallResult, root: str, index: dict[str, Any]) -> dict[str, ClaimSlotItem]:
    evidence_ids = {str(e.bead_id) for e in result.evidence if str(e.bead_id)}
    beads = index.get("beads") or {}
    pairs: set[tuple[str, str]] = set()
    for bid in evidence_ids:
        row = beads.get(bid) if isinstance(beads, dict) else None
        if not isinstance(row, dict):
            continue
        for claim in row.get("claims") or []:
            if isinstance(claim, dict) and _text(claim.get("subject")) and _text(claim.get("slot")):
                pairs.add((_text(claim.get("subject")), _text(claim.get("slot"))))
        for update in row.get("claim_updates") or []:
            if isinstance(update, dict) and _text(update.get("subject")) and _text(update.get("slot")):
                pairs.add((_text(update.get("subject")), _text(update.get("slot"))))
    if not pairs:
        return {}
    _, all_updates = read_all_claim_rows(root)
    slots: dict[str, ClaimSlotItem] = {}
    for subject, slot in sorted(pairs):
        state = resolve_current_state(root, subject, slot)
        current = state.get("current_claim") if isinstance(state.get("current_claim"), dict) else {}
        latest_update = _latest_slot_update(all_updates, subject, slot)
        key = f"{subject}:{slot}"
        slots[key] = ClaimSlotItem(
            key=key,
            subject=subject,
            slot=slot,
            current_value=current.get("value") if isinstance(current, dict) else None,
            status=_text(state.get("status")),
            current_claim_id=_text(current.get("id") if isinstance(current, dict) else ""),
            chain_seq=int(latest_update.get("chain_seq")) if str(latest_update.get("chain_seq") or "").isdigit() else None,
            grounding_hash=_text(latest_update.get("grounding_hash")) or None,
        )
    return slots


def _conflicts_for_result(result: RecallResult, root: str, index: dict[str, Any]) -> list[ConflictItem]:
    """Find active claim conflicts for all (subject, slot) pairs in evidence beads.

    Scoped to direct evidence only (one pass); ignores slots without conflict status.
    """
    from core_memory.claim.epistemic import conflict_score_for_pair

    evidence_ids = {str(e.bead_id) for e in result.evidence if str(e.bead_id)}
    beads = index.get("beads") or {}
    pairs: set[tuple[str, str]] = set()
    for bid in evidence_ids:
        row = beads.get(bid) if isinstance(beads, dict) else None
        if not isinstance(row, dict):
            continue
        for claim in row.get("claims") or []:
            if isinstance(claim, dict) and _text(claim.get("subject")) and _text(claim.get("slot")):
                pairs.add((_text(claim.get("subject")), _text(claim.get("slot"))))
        for update in row.get("claim_updates") or []:
            if isinstance(update, dict) and _text(update.get("subject")) and _text(update.get("slot")):
                pairs.add((_text(update.get("subject")), _text(update.get("slot"))))

    if not pairs:
        return []

    items: list[ConflictItem] = []
    for subject, slot in sorted(pairs):
        state = resolve_current_state(root, subject, slot)
        if str(state.get("status") or "") != "conflict":
            continue
        conflict_claims = state.get("conflicts") or []
        if len(conflict_claims) < 2:
            if len(conflict_claims) == 1:
                current = state.get("current_claim")
                if isinstance(current, dict) and current != conflict_claims[0]:
                    conflict_claims = [conflict_claims[0], current]
            if len(conflict_claims) < 2:
                continue
        claim_a = conflict_claims[0]
        claim_b = conflict_claims[1]
        score = conflict_score_for_pair(claim_a, claim_b)
        conflict_since = str(claim_a.get("created_at") or claim_b.get("created_at") or "")
        seq_a = int(claim_a.get("chain_seq") or 0) if str(claim_a.get("chain_seq") or "").isdigit() else 0
        seq_b = int(claim_b.get("chain_seq") or 0) if str(claim_b.get("chain_seq") or "").isdigit() else 0
        items.append(ConflictItem(
            subject=subject,
            slot=slot,
            claim_a_id=_text(claim_a.get("id")),
            claim_b_id=_text(claim_b.get("id")),
            epistemic_conflict_score=score,
            conflict_since=conflict_since,
            chain_seq_gap=abs(seq_b - seq_a),
            metadata={
                # Lightweight claim snapshots so a review prompt can show values
                # without re-reading the store.
                "claim_a": {"id": _text(claim_a.get("id")), "value": claim_a.get("value"), "created_at": _text(claim_a.get("created_at"))},
                "claim_b": {"id": _text(claim_b.get("id")), "value": claim_b.get("value"), "created_at": _text(claim_b.get("created_at"))},
            },
        ))

    return items


def _attach_conflict_reviews(result: RecallResult, root: str) -> None:
    """Emit contradiction candidates and attach render-agnostic review prompts.

    Only conflicts above the review threshold get an actionable prompt (with a
    candidate_id the agent can resolve). Conflicts the user already deferred are
    left without a prompt so the agent doesn't re-ask in-band.
    """
    if not result.conflicts:
        return
    from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
    from core_memory.claim.conflict_review import build_conflict_review

    res = enqueue_contradiction_pressure_candidates(root=root, conflicts=result.conflicts)
    candidate_ids = dict(res.get("candidate_ids") or {})
    deferred = set(res.get("deferred_keys") or [])

    for conflict in result.conflicts:
        slot_key = f"{conflict.subject}:{conflict.slot}"
        if slot_key in deferred:
            conflict.review_prompt = None
            continue
        cid = candidate_ids.get(slot_key)
        if not cid:
            continue  # below threshold → informational only, no actionable prompt
        meta = conflict.metadata or {}
        conflict.candidate_id = cid
        conflict.review_prompt = build_conflict_review(
            subject=conflict.subject,
            slot=conflict.slot,
            claim_a=dict(meta.get("claim_a") or {"id": conflict.claim_a_id}),
            claim_b=dict(meta.get("claim_b") or {"id": conflict.claim_b_id}),
            epistemic_conflict_score=conflict.epistemic_conflict_score,
            conflict_since=conflict.conflict_since,
            candidate_id=cid,
        )


def _enrich_recall_state(result: RecallResult, *, root: str, query: str) -> None:
    index = _read_index(root)
    if not index:
        return
    _add_evidence_grounding(result, index)
    result.resolved_goals = _resolved_goals_for_result(result, index, query)
    result.claim_slots = _claim_slots_for_result(result, root, index)
    result.conflicts = _conflicts_for_result(result, root, index)


def _expected_shape(query: str) -> dict[str, Any]:
    """Small deterministic query-shape hint for diagnostics.

    This is intentionally not the future dynamic-effort planner. It only gives
    callers visibility into the static assumptions that guided this recall call.
    """
    q = query.lower()
    bead_types: list[str] = []
    relations: list[str] = []
    if any(term in q for term in ["decision", "decide", "decided", "rationale"]):
        bead_types.extend(["decision", "rationale"])
        relations.extend(["superseded_by", "resolves", "supports"])
    if any(term in q for term in ["why", "cause", "caused", "because", "led to"]):
        relations.extend(["caused_by", "led_to", "supports"])
    if any(term in q for term in ["goal", "finished", "done", "outcome", "resolved"]):
        bead_types.extend(["goal", "outcome"])
        relations.extend(["resolves", "led_to"])
    temporal_terms = ["last week", "yesterday", "today", "when", "timeline", "history"]
    time_range_hint = "relative" if any(term in q for term in temporal_terms) else ""
    return {
        "bead_types": sorted(set(bead_types)),
        "relations": sorted(set(relations)),
        "time_range_hint": time_range_hint,
    }


def _normalize_request(
    query_or_request: str | dict[str, Any],
    *,
    effort: str,
    intent: str | None,
    k: int | None,
    speaker: str | None,
    request_overrides: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    defaults = _EFFORT_DEFAULTS[effort]
    if isinstance(query_or_request, str):
        query = query_or_request.strip()
        if not query:
            raise ValueError("recall query must be a non-empty string")
        request: dict[str, Any] = {"raw_query": query}
    elif isinstance(query_or_request, dict):
        request = dict(query_or_request)
        query = _query_text(request)
        if not query:
            raise ValueError("recall request must include a non-empty query")
        request.setdefault("raw_query", query)
    else:
        raise TypeError("recall query_or_request must be a string or dict")

    default_intent = "causal" if effort != "low" and _looks_causal_or_temporal(query) else "remember"
    request.setdefault("intent", intent or default_intent)
    request.setdefault("k", int(defaults["k"] if k is None else k))
    request.setdefault("effort", effort)
    request.setdefault("grounding_mode", defaults["grounding_mode"])
    hints = dict(request.get("hints") or request_overrides.get("hints") or {})
    if hints:
        request["hints"] = normalize_recall_hints(hints)
    if defaults.get("hydration") and not request.get("hydration"):
        request["hydration"] = dict(defaults["hydration"])
    if speaker is not None:
        request.setdefault("speaker", speaker)
        facets = dict(request.get("facets") or {})
        metadata = dict(facets.get("metadata") or {})
        metadata.setdefault("speaker", speaker)
        facets["metadata"] = metadata
        request.setdefault("facets", facets)
    # Soft structural hints parsed from the query (e.g. "what caused X" implies
    # caused_by/led_to edges). These only re-rank traversal chains, never filter,
    # so a wrong parse cannot hurt recall. Skipped for low effort (no traversal)
    # and never overrides hints a caller passed explicitly.
    if effort != "low":
        shape = _expected_shape(query)
        hint_relations = list(shape.get("relations") or [])
        if hint_relations:
            facets = dict(request.get("facets") or {})
            facets.setdefault("structural_hint_relations", hint_relations)
            request["facets"] = facets
    if request.get("hints"):
        facets = dict(request.get("facets") or {})
        facets.setdefault("hints", request["hints"])
        request["facets"] = facets
    request.update(request_overrides)
    if request.get("hints"):
        request["hints"] = normalize_recall_hints(dict(request.get("hints") or {}))
        facets = dict(request.get("facets") or {})
        facets["hints"] = request["hints"]
        request["facets"] = facets
    return query, request


# Per-relationship-type base weights for hop scoring.
# Causal/semantic edges carry topical signal; temporal/entity edges do not.
_RELATIONSHIP_HOP_WEIGHT: dict[str, float] = {
    # Causal — strongest signal for multi-hop retrieval
    "caused_by": 0.90, "causes": 0.90, "enables": 0.90, "results_in": 0.90,
    "resolves": 0.88, "diagnoses": 0.88,
    # Semantic — strong topical signal
    "supports": 0.85, "refines": 0.85, "supersedes": 0.85,
    "contradicts": 0.82, "validates": 0.82, "informed_by": 0.80,
    # Weak / generic
    "associated_with": 0.60, "related_to": 0.60, "shared_entity": 0.55,
    # Temporal — low signal (adjacency, not topical relevance)
    "follows": 0.35, "precedes": 0.35, "sequential_turn": 0.35,
    "continues": 0.45, "next_turn": 0.35, "prev_turn": 0.35,
}
_DEFAULT_HOP_WEIGHT = 0.70  # unknown / generic relationship
_HOP_DECAY = 0.80           # multiplicative per-hop decay


def _expand_via_association_hops(
    root: str,
    evidence: list["EvidenceItem"],
    hops: int,
    max_expansion: int = 16,
) -> list["EvidenceItem"]:
    """Widen the candidate set by walking association edges from vector seeds.

    low    → hops=0 (no expansion)
    medium → hops=1 (direct neighbours)
    high   → hops=2 (neighbours-of-neighbours)

    Scoring: each hop item receives
        score = seed_score × edge_weight × confidence × HOP_DECAY (per hop)

    where edge_weight is keyed by relationship type so causal/semantic edges
    rank higher than temporal or entity-overlap edges.  Strong 1-hop causal
    neighbours can therefore displace weak vector matches.
    """
    if hops <= 0 or not evidence:
        return evidence
    import json as _json
    from pathlib import Path as _Path

    index_file = _Path(root) / ".beads" / "index.json"
    try:
        index = _json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return evidence

    assoc_list = index.get("associations") or []
    beads_map = index.get("beads") or {}

    # Build weighted adjacency: node → [(neighbor, edge_score)]
    # edge_score = rel_weight × confidence (bidirectional)
    adj: dict[str, list[tuple[str, float]]] = {}
    for assoc in assoc_list:
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "").strip()
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "").strip()
        if not src or not tgt:
            continue
        rel = str(assoc.get("relationship") or "").strip().lower()
        rel_weight = _RELATIONSHIP_HOP_WEIGHT.get(rel, _DEFAULT_HOP_WEIGHT)
        raw_conf = assoc.get("confidence")
        conf = max(0.0, min(1.0, float(raw_conf))) if raw_conf is not None else 0.85
        edge_score = rel_weight * conf
        adj.setdefault(src, []).append((tgt, edge_score))
        adj.setdefault(tgt, []).append((src, edge_score))

    # BFS with best-path score propagation.
    # frontier maps bead_id → best score reaching it so far.
    known: set[str] = {str(e.bead_id) for e in evidence}
    frontier: dict[str, float] = {
        str(e.bead_id): float(e.score if e.score is not None else 0.1)
        for e in evidence
    }
    # best_score tracks the score to assign when appending a discovered item.
    best_discovered: dict[str, float] = {}

    for _ in range(hops):
        next_frontier: dict[str, float] = {}
        for seed_id, seed_score in frontier.items():
            for neighbor_id, edge_score in adj.get(seed_id, []):
                if neighbor_id in known:
                    continue
                candidate = seed_score * edge_score * _HOP_DECAY
                if candidate > next_frontier.get(neighbor_id, -1.0):
                    next_frontier[neighbor_id] = candidate
        if not next_frontier:
            break
        for nid, nscore in next_frontier.items():
            if nscore > best_discovered.get(nid, -1.0):
                best_discovered[nid] = nscore
        known |= set(next_frontier.keys())
        frontier = next_frontier

    if not best_discovered:
        return evidence

    # Sort discovered items by score descending so we add the strongest first.
    ranked = sorted(best_discovered.items(), key=lambda kv: -kv[1])

    out = list(evidence)
    added = 0
    for bid, score in ranked:
        if added >= max_expansion:
            break
        bead = beads_map.get(bid)
        if not isinstance(bead, dict):
            continue
        if not bead.get("retrieval_eligible", True):
            continue
        summary = bead.get("summary") or []
        excerpt = str(summary[0] if summary else (bead.get("detail") or ""))[:240]
        out.append(EvidenceItem(
            bead_id=bid,
            type=str(bead.get("type") or ""),
            title=str(bead.get("title") or ""),
            content_excerpt=excerpt,
            score=round(score, 4),
            reason="association_hop",
        ))
        added += 1
    # Re-sort the full list so hop items with competitive scores rank above
    # weak vector matches rather than always landing after them in the list.
    out.sort(key=lambda e: float("-inf") if e.score is None else -e.score)
    return out


def _read_myelination_manifest(root: str) -> dict[str, float]:
    try:
        p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
        if not p.exists():
            return {}
        payload = json.loads(p.read_text(encoding="utf-8"))
        return {
            str(k): float(v)
            for k, v in (payload.get("bonus_by_bead_id") or {}).items()
            if abs(float(v)) > 1e-9
        }
    except Exception:
        return {}


def _apply_myelination_bonuses(result: RecallResult, bonus_by_bead_id: dict[str, float]) -> None:
    if not bonus_by_bead_id:
        return
    for item in result.evidence:
        bonus = float(bonus_by_bead_id.get(str(item.bead_id) or "", 0.0))
        if abs(bonus) < 1e-9:
            continue
        base = float(item.score) if item.score is not None else 0.0
        item.score = round(min(1.0, max(0.0, base + bonus)), 6)
    result.evidence.sort(key=lambda e: float(e.score) if e.score is not None else 0.0, reverse=True)


def _filter_evidence_by_as_of(evidence: list[EvidenceItem], as_of_str: str) -> list[EvidenceItem]:
    from core_memory.temporal import normalize_as_of as _norm
    as_of_dt = _norm(as_of_str)
    if as_of_dt is None:
        return evidence
    filtered = []
    for item in evidence:
        created = str((item.metadata or {}).get("created_at") or "").strip()
        if not created:
            filtered.append(item)
            continue
        item_dt = _norm(created)
        if item_dt is None or item_dt <= as_of_dt:
            filtered.append(item)
    return filtered


def recall(
    query_or_request: str | dict[str, Any],
    *,
    effort: str = "medium",
    intent: str | None = None,
    k: int | None = None,
    speaker: str | None = None,
    as_of: str | None = None,
    root: str = ".",
    explain: bool = True,
    include_raw: bool = True,
    **request_overrides: Any,
) -> RecallResult:
    """Single-verb grounded recall orchestrator.

    Public callers choose effort (`low`, `medium`, `high`). Core Memory chooses
    internal retrieval tiers and reports what happened via `RecallResult.tier_path`
    and `RecallResult.steps`.
    """
    selected_effort = validate_recall_effort(effort)
    if selected_effort == "dynamic":
        raise ValueError('effort="dynamic" is reserved for a future query-planning mode; use low, medium, or high')

    if as_of is not None:
        from core_memory.temporal import normalize_as_of as _norm
        if _norm(as_of) is None:
            raise ValueError(f"as_of must be a valid ISO 8601 timestamp, got {as_of!r}")
        # Inflate k by 1.5x so the semantic tier fetches extra candidates before
        # the post-filter discards beads created after as_of. Cap at 50.
        _base_k = int(k if k is not None else _EFFORT_DEFAULTS[selected_effort]["k"])
        _inflated_k = min(int(_base_k * 1.5 + 0.5), 50)
        request_overrides = {**request_overrides, "as_of": as_of, "k": _inflated_k}

    query, request = _normalize_request(
        query_or_request,
        effort=selected_effort,
        intent=intent,
        k=k,
        speaker=speaker,
        request_overrides=request_overrides,
    )
    raw = memory_execute(request=request, root=root, explain=explain)

    # Fire-and-forget retrieval telemetry — never let recording break recall.
    try:
        from core_memory.runtime.observability.retrieval_feedback import record_retrieval_feedback
        record_retrieval_feedback(root, request=request, response=raw)
    except Exception:
        pass

    result = recall_result_from_memory_execute(raw, query=query, effort=selected_effort, include_raw=include_raw)

    # Effort-gated association-hop expansion: medium adds 1-hop neighbours,
    # high adds 2-hop — so candidate sets grow monotonically with effort.
    # Runs after vector recall so it augments rather than replaces grounded results.
    _hops = int(_EFFORT_DEFAULTS[selected_effort].get("association_hops") or 0)
    if _hops > 0 and result.evidence:
        try:
            result.evidence = _expand_via_association_hops(root, result.evidence, hops=_hops)
        except Exception:
            pass

    _enrich_recall_state(result, root=root, query=query)

    # Emit contradiction candidates and attach render-agnostic review prompts so
    # the agent can surface high-pressure conflicts to the user in-band.
    try:
        _attach_conflict_reviews(result, root)
    except Exception:
        pass

    # Apply pre-computed myelination bonuses when enabled.
    try:
        from core_memory.runtime.observability.myelination import myelination_enabled
        if myelination_enabled():
            bonus_by_bead_id = _read_myelination_manifest(root)
            if bonus_by_bead_id:
                _apply_myelination_bonuses(result, bonus_by_bead_id)
    except Exception:
        pass

    # Multi-store fan-out: only activate when at least one external adapter is configured.
    try:
        from core_memory.config.feature_flags import external_pipehouse_url, external_ragie_api_key
        from core_memory.retrieval.fanout import fanout_recall
        _ragie_key = external_ragie_api_key()
        _pipehouse_url = external_pipehouse_url()
        _ragie_cfg = {"api_key": _ragie_key} if _ragie_key else None
        _pipehouse_cfg = {"base_url": _pipehouse_url} if _pipehouse_url else None
        if _ragie_cfg or _pipehouse_cfg:
            result = fanout_recall(
                query,
                core_memory_result=result,
                ragie_cfg=_ragie_cfg,
                pipehouse_cfg=_pipehouse_cfg,
            )
    except Exception:
        import logging
        logging.getLogger(__name__).warning("fanout_recall failed", exc_info=True)

    if as_of is not None:
        result.evidence = _filter_evidence_by_as_of(result.evidence, as_of)
        result.as_of = as_of
        result.metadata["as_of"] = as_of
    req_intent = str(request.get("intent") or intent or "")
    if should_run_causal_pipeline(query, selected_effort, req_intent):
        try:
            result = attach_causal_recall_pipeline(
                result,
                root=root,
                query=query,
                hints=dict(request.get("hints") or {}),
                max_depth=int(request.get("trace_max_depth") or request.get("max_depth") or 6),
                max_paths=int(request.get("trace_max_paths") or request.get("max_paths") or 20),
            )
        except Exception:
            result.warnings.append("causal_recall_pipeline_error")
    result.planning = RecallPlanning(
        selected_effort=selected_effort,
        reason=str(_EFFORT_DEFAULTS[selected_effort]["planning_reason"]),
        expected_shape=_expected_shape(query),
    )
    if result.steps:
        result.steps[0].metadata = {
            **dict(result.steps[0].metadata or {}),
            "effort": selected_effort,
            "grounding_mode": request.get("grounding_mode"),
        }
    else:
        result.steps.append(
            RecallStep(
                tier="semantic",
                query=query,
                status="ok" if raw.get("ok", True) else "failed",
                result_count=len(result.evidence),
                metadata={"effort": selected_effort, "grounding_mode": request.get("grounding_mode")},
            )
        )
    return result
