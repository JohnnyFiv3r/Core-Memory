from __future__ import annotations

from pathlib import Path
from typing import Any
import os
from datetime import datetime
import json

from core_memory.graph.api import causal_traverse
from core_memory.retrieval.normalize import classify_intent
from core_memory.retrieval.semantic_index import semantic_lookup
from core_memory.retrieval.visible_corpus import build_visible_corpus
from core_memory.integrations.api import hydrate_bead_sources


NON_FULL_GROUNDING_RELATIONSHIPS = {"follows", "precedes", "associated_with"}
PUBLIC_HYDRATION_TURN_SOURCES = {"cited_turns", "cited_turns_plus_adjacent"}


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

    mode_raw = str(req.get("turn_sources") or "cited_turns").strip().lower()
    if mode_raw not in PUBLIC_HYDRATION_TURN_SOURCES:
        if mode_raw:
            warnings.append(f"hydration_turn_sources_normalized:{mode_raw}->cited_turns")
        mode = "cited_turns"
    else:
        mode = mode_raw

    max_beads = max(1, int(req.get("max_beads") or 10))
    before = max(0, int(req.get("adjacent_before") or 0))
    after = max(0, int(req.get("adjacent_after") or 0))

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


def _to_anchor(res: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bid = str(res.get("bead_id") or "")
    row = by_id.get(bid) or {}
    bead = row.get("bead") or {}
    return {
        "bead_id": bid,
        "title": str(bead.get("title") or ""),
        "type": str(bead.get("type") or ""),
        "snippet": " ".join((bead.get("summary") or [])[:2]),
        "score": float(res.get("score") or 0.0),
        "semantic_score": float(res.get("score") or 0.0),
        "anchor_reason": str(res.get("anchor_reason") or "retrieved"),
        "context_bias_score": float(res.get("context_bias_score") or 0.0),
        "source_surface": str(res.get("source_surface") or row.get("source_surface") or "projection"),
        "status": str(res.get("status") or row.get("status") or ""),
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


def _apply_typed_filters(
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    projection_created_at: dict[str, str],
    submission: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    s = dict(submission or {})
    warnings: list[str] = []

    incident_id = str(s.get("incident_id") or "").strip()
    topic_keys = {str(x).strip().lower() for x in (s.get("topic_keys") or []) if str(x).strip()}
    bead_types = {str(x).strip().lower() for x in (s.get("bead_types") or []) if str(x).strip()}
    must_terms = [str(x).strip().lower() for x in (s.get("must_terms") or []) if str(x).strip()]
    avoid_terms = [str(x).strip().lower() for x in (s.get("avoid_terms") or []) if str(x).strip()]

    tr = dict(s.get("time_range") or {})
    tr_from_raw = str(tr.get("from") or "").strip()
    tr_to_raw = str(tr.get("to") or "").strip()
    tr_from = _parse_iso(tr_from_raw)
    tr_to = _parse_iso(tr_to_raw)
    if (tr_from_raw and tr_from is None) or (tr_to_raw and tr_to is None):
        warnings.append("invalid_time_range_ignored")
        tr_from = None
        tr_to = None

    if not any([incident_id, topic_keys, bead_types, must_terms, avoid_terms, tr_from, tr_to]):
        return rows, warnings

    out: list[dict[str, Any]] = []
    for r in rows:
        bid = str(r.get("bead_id") or "")
        row = by_id.get(bid) or {}
        bead = row.get("bead") or {}

        if incident_id and str(bead.get("incident_id") or "") != incident_id:
            continue

        if topic_keys:
            bead_topics = {str(x).strip().lower() for x in (bead.get("tags") or []) if str(x).strip()}
            bead_topics.update({str(x).strip().lower() for x in (bead.get("topics") or []) if str(x).strip()})
            if not bead_topics.intersection(topic_keys):
                continue

        if bead_types and str(bead.get("type") or "").strip().lower() not in bead_types:
            continue

        if tr_from or tr_to:
            bts = _parse_iso(str(projection_created_at.get(bid) or row.get("created_at") or bead.get("created_at") or ""))
            if bts is None:
                continue
            if tr_from and bts < tr_from:
                continue
            if tr_to and bts > tr_to:
                continue

        text_blob = " ".join(
            [
                str(bead.get("title") or ""),
                " ".join(str(x) for x in (bead.get("summary") or [])),
                str(bead.get("detail") or ""),
                str(row.get("semantic_text") or ""),
                str(row.get("lexical_text") or ""),
            ]
        ).lower()

        if must_terms and not all(t in text_blob for t in must_terms):
            continue
        if avoid_terms and any(t in text_blob for t in avoid_terms):
            continue

        out.append(r)

    return out, warnings


def search_request(
    *,
    root: str | Path,
    query: str,
    k: int = 10,
    intent: str = "remember",
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rp = Path(root)
    corpus = build_visible_corpus(rp)
    by_id = {str(r.get("bead_id") or ""): r for r in corpus}
    projection_created_at: dict[str, str] = {}
    try:
        idx = json.loads((rp / ".beads" / "index.json").read_text(encoding="utf-8"))
        for bid, bead in ((idx.get("beads") or {}) if isinstance(idx, dict) else {}).items():
            if isinstance(bead, dict):
                projection_created_at[str(bid)] = str(bead.get("created_at") or "")
    except Exception:
        projection_created_at = {}

    sem = semantic_lookup(rp, query, k=max(24, int(k) * 2))
    sem_rows = [dict(r or {}) for r in (sem.get("results") or [])]
    for r in sem_rows:
        r.setdefault("anchor_reason", "retrieved")
        r.setdefault("source_surface", (by_id.get(str(r.get("bead_id") or ""), {}) or {}).get("source_surface", "projection"))

    strong_sem = [r for r in sem_rows if float(r.get("score") or 0.0) >= 0.55 and r.get("anchor_reason") == "retrieved"]
    if len(strong_sem) < 3:
        rescue = _lexical_rescue(query, corpus, max_add=2)
        seen = {str(r.get("bead_id") or "") for r in sem_rows}
        for rr in rescue:
            if rr["bead_id"] not in seen:
                sem_rows.append(rr)

    sem_rows.sort(
        key=lambda r: (
            0 if str(r.get("anchor_reason") or "") == "pinned" else (1 if str(r.get("anchor_reason") or "") == "strict_facet_match" else 2),
            -float(r.get("score") or 0.0),
            -float(r.get("context_bias_score") or 0.0),
            _status_rank(str(r.get("status") or "")),
            str((by_id.get(str(r.get("bead_id") or ""), {}) or {}).get("created_at") or ""),
            str(r.get("bead_id") or ""),
        )
    )

    sem_rows, filter_warnings = _apply_typed_filters(sem_rows, by_id, projection_created_at, submission)

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
        "anchors": anchors,
        "results": anchors,  # compatibility alias
        "chains": [],
        "citations": [],
        "confidence": confidence,
        "next_action": next_action,
        "warnings": list(sem.get("warnings") or []) + list(filter_warnings),
        "snapped": {
            "raw_query": query,
            "intent": intent,
            "k": int(k),
            **{
                key: value
                for key, value in dict(submission or {}).items()
                if key in {"incident_id", "topic_keys", "bead_types", "relation_types", "must_terms", "avoid_terms", "time_range", "require_structural"}
            },
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
) -> dict[str, Any]:
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

    anchors = anchors_out.get("anchors") or []
    a_ids = [str(a.get("bead_id") or "") for a in anchors[:5] if str(a.get("bead_id") or "")]
    trav = causal_traverse(Path(root), anchor_ids=a_ids, max_depth=3, max_chains=5) if a_ids else {"ok": True, "chains": []}
    chains = list(trav.get("chains") or [])
    relation_filter = {str(x).strip().lower() for x in ((submission or {}).get("relation_types") or []) if str(x).strip()}
    if relation_filter:
        chains = [
            c
            for c in chains
            if {str((e or {}).get("rel") or "").strip().lower() for e in (c.get("edges") or [])}.intersection(relation_filter)
        ]

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

    out = {
        "ok": True,
        "anchors": anchors,
        "results": anchors,  # compatibility alias
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
    }

    hyd_req = dict(hydration or {})
    if hyd_req:
        status = "complete"
        hcfg, hw = _normalize_public_hydration_request(hyd_req)
        try:
            bead_ids = [str(a.get("bead_id") or "") for a in (out.get("anchors") or []) if str(a.get("bead_id") or "")]
            h = hydrate_bead_sources(
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
        submission = {
            "query_text": query,
            "intent": intent,
            "k": k,
            "incident_id": str((facets.get("incident_ids") or [None])[0] or "").strip() or None,
            "topic_keys": list(facets.get("topic_keys") or []),
            "bead_types": list(facets.get("bead_types") or []),
            "relation_types": list(facets.get("relation_types") or []),
            "must_terms": list(facets.get("must_terms") or []),
            "avoid_terms": list(facets.get("avoid_terms") or []),
            "time_range": dict(facets.get("time_range") or {}),
            "require_structural": bool(constraints.get("require_structural", False)),
        }
        out = search_request(root=root, query=query, k=k, intent=intent, submission=submission)
        out["grounding"] = {"required": False, "achieved": False, "level": "none", "reason": "search_only"}
        out.setdefault("hydration", {"status": "not_requested", "warnings": []})
    else:
        facets = dict(req.get("facets") or {})
        submission = {
            "query_text": query,
            "intent": intent,
            "k": k,
            "incident_id": str((facets.get("incident_ids") or [None])[0] or "").strip() or None,
            "topic_keys": list(facets.get("topic_keys") or []),
            "bead_types": list(facets.get("bead_types") or []),
            "relation_types": list(facets.get("relation_types") or []),
            "must_terms": list(facets.get("must_terms") or []),
            "avoid_terms": list(facets.get("avoid_terms") or []),
            "time_range": dict(facets.get("time_range") or {}),
            "require_structural": bool(constraints.get("require_structural", False)),
        }
        out = trace_request(
            root=root,
            query=query,
            anchor_ids=req.get("anchor_ids") or None,
            k=k,
            intent=intent,
            hydration=req.get("hydration") or None,
            submission=submission,
        )

    out.setdefault("chains", [])
    out.setdefault("citations", [])

    # explicit best-effort hydration (post-selection, non-fatal) for search_only path
    hyd_req = dict(req.get("hydration") or {})
    if hyd_req and grounding_mode == "search_only":
        status = "complete"
        hcfg, hw = _normalize_public_hydration_request(hyd_req)
        try:
            bead_ids = [str(a.get("bead_id") or "") for a in (out.get("anchors") or []) if str(a.get("bead_id") or "")]
            h = hydrate_bead_sources(
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

    if explain:
        out["explain"] = {"planner": "canonical_v9", "stages": ["normalize", "anchors", "trace_or_not", "finalize"]}
    return out
