"""
Promotion policy: scoring, thresholding, and candidate evaluation.

Extracted from store.py per Codex Phase 2 refactor.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Type-prior scores for promotion
BEAD_TYPE_PRIORS = {
    "design_principle": 0.72,
    "precedent": 0.7,
    "decision": 0.66,
    "lesson": 0.62,
    "outcome": 0.6,
    "evidence": 0.58,
    "goal": 0.56,
    "context": 0.35,
    "checkpoint": 0.35,
}

DEFAULT_THRESHOLD = 0.72
MIN_THRESHOLD = 0.68
MAX_THRESHOLD = 0.92


def _promotion_state(bead: dict) -> str:
    state = str((bead or {}).get("promotion_state") or "").strip().lower()
    if state:
        return state
    status = str((bead or {}).get("status") or "").strip().lower()
    if status in {"candidate", "promoted"}:
        return status
    return ""


def _has_evidence(bead: dict) -> bool:
    """Check if bead has evidence references."""
    return bool((bead.get("evidence_refs") or []) or (bead.get("tool_output_ids") or []) or (bead.get("tool_output_id") or "").strip())


def _normalize_links(links) -> list[dict]:
    """Normalize links to canonical list format."""
    if links is None:
        return []
    out = []
    if isinstance(links, list):
        for row in links:
            if not isinstance(row, dict):
                continue
            ltype = str(row.get("type") or "").strip()
            bid = str(row.get("bead_id") or row.get("id") or "").strip()
            if ltype and bid:
                out.append({"type": ltype, "bead_id": bid})
        return out
    if isinstance(links, dict):
        for k, v in links.items():
            if isinstance(v, list):
                for bid in v:
                    b = str(bid or "").strip()
                    if b:
                        out.append({"type": str(k), "bead_id": b})
            else:
                b = str(v or "").strip()
                if b:
                    out.append({"type": str(k), "bead_id": b})
    return out


def _reinforcement_signals(index: dict, bead: dict) -> dict:
    """Calculate reinforcement signals for a bead."""
    bead_id = str(bead.get("id") or "")
    if not bead_id:
        return {"count": 0}

    bead_links = _normalize_links(bead.get("links"))
    links_in = 0
    links_out = len(bead_links)
    
    for other in (index.get("beads") or {}).values():
        if other.get("id") == bead_id:
            continue
        if str(other.get("linked_bead_id") or "") == bead_id:
            links_in += 1
            continue
        for l in _normalize_links(other.get("links")):
            if str((l or {}).get("bead_id") or "") == bead_id:
                links_in += 1
                break

    assoc_deg = 0
    for a in (index.get("associations") or []):
        if not (a.get("source_bead") == bead_id or a.get("target_bead") == bead_id):
            continue
        edge_class = str(a.get("edge_class") or "").lower()
        rel = str(a.get("relationship") or "").lower()
        if edge_class == "derived" and rel in {"shared_tag", "related", "follows"}:
            continue
        assoc_deg += 1

    recurrence = len(bead.get("source_turn_ids") or []) >= 2
    recalled = int(bead.get("recall_count") or 0) > 0

    cnt = 0
    for v in [links_in > 0 or links_out > 0, assoc_deg > 0, recurrence, recalled]:
        cnt += 1 if v else 0

    return {
        "links_in": links_in,
        "links_out": links_out,
        "association_degree": assoc_deg,
        "recurrence": recurrence,
        "recalled": recalled,
        "count": cnt,
    }


def compute_promotion_score(index: dict, bead: dict) -> tuple[float, dict]:
    """Compute promotion score for a bead.
    
    Returns (score, factors_dict).
    """
    t = str(bead.get("type") or "").lower()
    score = BEAD_TYPE_PRIORS.get(t, 0.4)

    has_evidence = _has_evidence(bead)
    detail_len = len((bead.get("detail") or "").strip())
    has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
    
    if has_evidence:
        score += 0.12
    if detail_len >= 80:
        score += 0.1
    if has_link:
        score += 0.08

    rs = _reinforcement_signals(index, bead)
    score += min(0.16, 0.03 * float(rs.get("association_degree", 0)))
    if rs.get("recurrence"):
        score += 0.06
    if rs.get("recalled"):
        score += 0.05
    if rs.get("links_in", 0) > 0:
        score += 0.05

    if t == "outcome" and str(bead.get("linked_bead_id") or "").strip():
        score += 0.05

    created_at = str(bead.get("created_at") or "")
    freshness = 0.0
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
            freshness = 0.05 if age_days <= 2.0 else 0.0
        except ValueError:
            freshness = 0.0
    score += freshness

    score = max(0.0, min(1.0, score))
    return score, {
        "has_evidence": has_evidence,
        "detail_len": detail_len,
        "has_link": has_link,
        "freshness": freshness,
        "reinforcement": rs,
    }


def compute_adaptive_threshold(index: dict) -> float:
    """Compute adaptive promotion threshold based on current promoted ratio."""
    beads = list((index.get("beads") or {}).values())
    if not beads:
        return DEFAULT_THRESHOLD
    
    promoted = sum(1 for b in beads if _promotion_state(b) == "promoted")
    ratio = promoted / max(1, len(beads))
    
    thr = DEFAULT_THRESHOLD
    if ratio > 0.25:
        thr += min(0.2, (ratio - 0.25) * 0.6)
    
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, thr))


def is_candidate_promotable(index: dict, bead: dict) -> tuple[bool, dict]:
    """Check if a candidate bead meets promotion criteria.
    
    Returns (is_promotable, metadata_dict).
    """
    score, factors = compute_promotion_score(index, bead)
    threshold = compute_adaptive_threshold(index)
    reinforcement_count = int((factors.get("reinforcement") or {}).get("count", 0))
    
    allow = score >= threshold and reinforcement_count >= 1
    reason = "score+reinforcement" if allow else "insufficient_score_or_reinforcement"
    
    meta = {
        "score": round(score, 4),
        "threshold": round(threshold, 4),
        "reinforcement_count": reinforcement_count,
        "reason": reason,
    }
    return allow, meta


def get_recommendation_rows(
    index: dict, 
    query_text: str = "",
    query_tokenize_fn: Optional[callable] = None,
    query_expand_fn: Optional[callable] = None,
) -> tuple[list[dict], float]:
    """Get recommendation rows for all candidate beads.
    
    Returns (rows, threshold).
    """
    beads = list((index.get("beads") or {}).values())
    threshold = compute_adaptive_threshold(index)
    
    if query_tokenize_fn and query_expand_fn:
        q_tokens = query_expand_fn(query_text, query_tokenize_fn(query_text), max_extra=12)
    else:
        q_tokens = set()

    rows = []
    for bead in beads:
        if _promotion_state(bead) != "candidate":
            continue
        
        score, factors = compute_promotion_score(index, bead)
        reinf = int((factors.get("reinforcement") or {}).get("count", 0))
        
        # Compute query overlap
        if q_tokens:
            title = (bead.get("title") or "").lower()
            summary = " ".join(bead.get("summary") or []).lower()
            text_tokens = set(title.split()) | set(summary.split())
            q_overlap = len(q_tokens.intersection(text_tokens))
        else:
            q_overlap = 0

        if score >= threshold and reinf >= 1:
            rec = "strong"
        elif score >= max(0.6, threshold - 0.08):
            rec = "review"
        else:
            rec = "hold"

        rows.append({
            "bead_id": bead.get("id"),
            "type": bead.get("type"),
            "title": bead.get("title"),
            "summary": (bead.get("summary") or [])[:2],
            "promotion_score": round(score, 4),
            "promotion_threshold": round(threshold, 4),
            "recommendation": rec,
            "query_overlap": q_overlap,
            "reinforcement": factors.get("reinforcement") or {},
            "has_evidence": bool(factors.get("has_evidence")),
            "has_link": bool(factors.get("has_link")),
            "detail_len": int(factors.get("detail_len") or 0),
            "created_at": bead.get("created_at"),
        })

    rows = sorted(rows, key=lambda r: (r.get("query_overlap", 0), r.get("promotion_score", 0.0), r.get("created_at") or ""), reverse=True)
    return rows, threshold
