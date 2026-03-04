from __future__ import annotations

from .config import MIN_STRUCTURAL_TOPK, QUALITY_THRESHOLD, TOPK_STRUCTURAL_CHECK


def quality_gate_decision(results: list[dict]) -> dict:
    if not results:
        return {"retry": True, "reason": "no_results"}

    top = results[0]
    top_score = float(top.get("rerank_score") or 0.0)
    if top_score < QUALITY_THRESHOLD:
        return {"retry": True, "reason": "top_score_below_threshold", "top_score": top_score}

    topk = results[: max(1, int(TOPK_STRUCTURAL_CHECK))]
    sq = []
    for r in topk:
        f = r.get("features") or {}
        sq.append((float(f.get("has_decision") or 0) + float(f.get("has_evidence") or 0) + float(f.get("has_outcome") or 0)) / 3.0)
    avg_sq = sum(sq) / max(1, len(sq))
    if avg_sq < MIN_STRUCTURAL_TOPK:
        return {"retry": True, "reason": "low_structural_quality_topk", "avg_structural_quality": round(avg_sq, 4)}

    return {"retry": False, "reason": "ok", "top_score": top_score, "avg_structural_quality": round(avg_sq, 4)}
