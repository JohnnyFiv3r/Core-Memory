from __future__ import annotations

import json
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.graph import causal_traverse


def _load_beads(root: Path) -> dict:
    p = root / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("beads") or {}
    except Exception:
        return {}


def search_typed(root: Path, form: dict, include_explain: bool = False) -> dict:
    beads = _load_beads(root)
    q = str(form.get("query_text") or "").strip()
    qx = q
    if form.get("must_terms"):
        qx = (qx + " " + " ".join([str(x) for x in form.get("must_terms") or []])).strip()

    k = int(form.get("k") or 10)
    intent = str(form.get("intent") or "other")
    intent_bucket = intent if intent in {"causal", "remember", "what_changed", "when"} else "remember"

    h = hybrid_lookup(root, query=qx, k=max(20, k * 4))
    if not h.get("ok"):
        return {"ok": False, "error": h.get("error")}
    rr = rerank_candidates(root, query=qx, candidates=h.get("results") or [], intent_class=intent_bucket)
    ranked = rr.get("results") or []

    # deterministic filters
    incident_id = str(form.get("incident_id") or "")
    topic_keys = set([str(x) for x in (form.get("topic_keys") or [])])
    bead_types = set([str(x) for x in (form.get("bead_types") or [])])
    avoid_terms = [str(x).lower() for x in (form.get("avoid_terms") or [])]

    filtered = []
    for r in ranked:
        bid = str(r.get("bead_id") or "")
        b = beads.get(bid) or {}
        if incident_id and str(b.get("incident_id") or "") != incident_id:
            continue
        if topic_keys:
            tags = set([str(t) for t in (b.get("tags") or [])])
            if not tags.intersection(topic_keys):
                continue
        if bead_types and str(b.get("type") or "") not in bead_types:
            continue
        txt = (str(b.get("title") or "") + " " + " ".join(b.get("summary") or [])).lower()
        if any(t and t in txt for t in avoid_terms):
            continue
        filtered.append(r)

    sel = filtered[:k]
    result_rows = []
    for r in sel:
        bid = str(r.get("bead_id") or "")
        b = beads.get(bid) or {}
        result_rows.append({
            "bead_id": bid,
            "title": str(b.get("title") or b.get("snapshot_title") or ""),
            "type": str(b.get("type") or ""),
            "snippet": " ".join((b.get("summary") or [])[:2]),
            "score": float(r.get("rerank_score") or r.get("fused_score") or 0.0),
        })

    chains = []
    relation_filter = set([str(x) for x in (form.get("relation_types") or [])])
    if form.get("require_structural") and result_rows:
        anchors = [x["bead_id"] for x in result_rows[:5]]
        trav = causal_traverse(root, anchor_ids=anchors, max_depth=2, max_chains=5)
        for c in (trav.get("chains") or []):
            edges = c.get("edges") or []
            if relation_filter:
                rels = set([str(e.get("rel") or "") for e in edges])
                if not rels.intersection(relation_filter):
                    continue
            chains.append({"path": c.get("path") or [], "edges": edges, "score": c.get("score")})
            if len(chains) >= 3:
                break

    warnings = []
    if not form.get("incident_id") and not (form.get("topic_keys") or []):
        warnings.append("no_strong_anchor_match_free_text_mode")
    if form.get("require_structural") and not chains:
        warnings.append("require_structural_requested_but_no_chains")

    avg_score = 0.0
    if result_rows:
        avg_score = sum(float(r.get("score") or 0.0) for r in result_rows[: min(3, len(result_rows))]) / max(1, min(3, len(result_rows)))

    has_anchor = bool(form.get("incident_id")) or bool(form.get("topic_keys") or [])
    if len(result_rows) >= min(3, k) and avg_score >= 0.45 and has_anchor:
        confidence = "high"
    elif result_rows:
        confidence = "medium"
    else:
        confidence = "low"

    if confidence == "high":
        suggested_next = "answer"
    elif result_rows:
        suggested_next = "broaden"
    else:
        suggested_next = "ask_clarifying"

    out = {
        "ok": True,
        "results": result_rows,
        "chains": chains,
        "snapped_query": form,
        "warnings": warnings,
        "confidence": confidence,
        "suggested_next": suggested_next,
    }
    if include_explain:
        out["retrieval_debug"] = {"hybrid": h, "rerank": rr, "filtered_count": len(filtered)}
    return out
