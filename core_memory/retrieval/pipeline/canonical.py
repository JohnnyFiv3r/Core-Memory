from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph.api import causal_traverse
from core_memory.retrieval.normalize import classify_intent
from core_memory.retrieval.semantic_index import semantic_lookup
from core_memory.retrieval.visible_corpus import build_visible_corpus


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


def search_request(*, root: str | Path, query: str, k: int = 10, intent: str = "remember") -> dict[str, Any]:
    rp = Path(root)
    corpus = build_visible_corpus(rp)
    by_id = {str(r.get("bead_id") or ""): r for r in corpus}

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

    anchors = [_to_anchor(r, by_id) for r in sem_rows[: max(1, int(k))]]
    confidence = "high" if anchors and float(anchors[0].get("semantic_score") or 0.0) >= 0.75 else ("medium" if anchors else "low")
    next_action = "answer" if confidence in {"high", "medium"} else "ask_clarifying"

    return {
        "ok": True,
        "anchors": anchors,
        "results": anchors,  # compatibility alias
        "chains": [],
        "citations": [],
        "confidence": confidence,
        "next_action": next_action,
        "warnings": list(sem.get("warnings") or []),
        "snapped": {"raw_query": query, "intent": intent, "k": int(k)},
    }


def trace_request(*, root: str | Path, query: str = "", anchor_ids: list[str] | None = None, k: int = 10, intent: str = "causal") -> dict[str, Any]:
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
        anchors_out = search_request(root=root, query=query, k=k, intent=intent)

    anchors = anchors_out.get("anchors") or []
    a_ids = [str(a.get("bead_id") or "") for a in anchors[:5] if str(a.get("bead_id") or "")]
    trav = causal_traverse(Path(root), anchor_ids=a_ids, max_depth=3, max_chains=5) if a_ids else {"ok": True, "chains": []}
    chains = list(trav.get("chains") or [])

    grounding = "full" if chains else "none"
    next_action = "answer" if grounding in {"full", "partial"} else "ask_clarifying"
    confidence = "high" if chains else ("medium" if anchors else "low")

    citations = []
    for c in chains[:3]:
        for b in (c.get("beads") or []):
            bid = str((b or {}).get("id") or "")
            if bid and bid not in {x.get("bead_id") for x in citations}:
                citations.append({"bead_id": bid, "title": str((b or {}).get("title") or ""), "type": str((b or {}).get("type") or "")})

    return {
        "ok": True,
        "anchors": anchors,
        "results": anchors,  # compatibility alias
        "chains": chains,
        "citations": citations,
        "grounding": {"required": True, "achieved": bool(chains), "level": grounding, "reason": "grounded" if chains else "none"},
        "confidence": confidence,
        "next_action": next_action,
        "warnings": list(anchors_out.get("warnings") or []),
        "snapped": anchors_out.get("snapped") or {"raw_query": query, "intent": intent, "k": int(k)},
    }


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
        out = search_request(root=root, query=query, k=k, intent=intent)
        out["grounding"] = {"required": False, "achieved": False, "level": "none", "reason": "search_only"}
    else:
        out = trace_request(root=root, query=query, anchor_ids=req.get("anchor_ids") or None, k=k, intent=intent)

    out.setdefault("chains", [])
    out.setdefault("citations", [])

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
    if explain:
        out["explain"] = {"planner": "canonical_v9", "stages": ["normalize", "anchors", "trace_or_not", "finalize"]}
    return out
