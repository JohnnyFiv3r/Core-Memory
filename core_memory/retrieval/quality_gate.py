from __future__ import annotations

from .config import (
    CAUSAL_MIN_STRUCTURAL_QUALITY,
    QUALITY_THRESHOLD_LONG,
    QUALITY_THRESHOLD_SHORT,
    SHORT_QUERY_TOKENS,
    TOPK_STRUCTURAL_CHECK,
)


def _is_causal_query(query: str) -> bool:
    q = (query or "").lower()
    cues = ["why", "decide", "because", "rationale"]
    return any(c in q for c in cues)


def quality_gate_decision(results: list[dict], query: str = "") -> dict:
    if not results:
        return {"retry": True, "reason": "no_results"}

    token_count = len((query or "").split())
    threshold = QUALITY_THRESHOLD_SHORT if token_count <= SHORT_QUERY_TOKENS else QUALITY_THRESHOLD_LONG

    top = results[0]
    top_score = float(top.get("rerank_score") or 0.0)
    if top_score < threshold:
        return {"retry": True, "reason": "top_score_below_threshold", "top_score": top_score, "threshold": threshold}

    topk = results[: max(1, int(TOPK_STRUCTURAL_CHECK))]
    sq = []
    for r in topk:
        d = r.get("derived") or {}
        sq.append(float(d.get("structural_quality") or 0.0))
    avg_sq = sum(sq) / max(1, len(sq))

    if _is_causal_query(query):
        top_struct = float((top.get("derived") or {}).get("structural_quality") or 0.0)
        if top_struct < CAUSAL_MIN_STRUCTURAL_QUALITY:
            return {
                "retry": True,
                "reason": "causal_min_structural_not_met",
                "top_structural_quality": round(top_struct, 4),
                "required": CAUSAL_MIN_STRUCTURAL_QUALITY,
            }

    return {
        "retry": False,
        "reason": "ok",
        "top_score": top_score,
        "threshold": threshold,
        "avg_structural_quality": round(avg_sq, 4),
        "causal_query": _is_causal_query(query),
    }
