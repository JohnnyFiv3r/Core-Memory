from __future__ import annotations

import json
from pathlib import Path

from .config import W_COVERAGE, W_FUSED, W_PENALTY, W_STRUCTURAL


def _tokenize(text: str) -> set[str]:
    return {t for t in (text or "").lower().replace("_", " ").replace("-", " ").split() if len(t) >= 3}


def _bead_text(bead: dict) -> str:
    return " ".join([
        str(bead.get("title") or ""),
        " ".join(bead.get("summary") or []),
        " ".join(bead.get("tags") or []),
    ])


def _features_for(bead: dict, query_tokens: set[str]) -> dict:
    btype = str(bead.get("type") or "")
    text_toks = _tokenize(_bead_text(bead))
    cov = len(query_tokens.intersection(text_toks)) / max(1, len(query_tokens)) if query_tokens else 0.0

    title = str(bead.get("title") or "").lower()
    low_info = (not title) or ("[[reply_to_current]]" in title) or ("auto-compaction complete" in title)
    has_edges = bool((bead.get("links") or []))

    feats = {
        "has_decision": 1 if btype == "decision" else 0,
        "has_evidence": 1 if btype == "evidence" else 0,
        "has_outcome": 1 if btype == "outcome" else 0,
        "has_structural_edges": 1 if has_edges else 0,
        "query_term_coverage": round(max(0.0, min(1.0, cov)), 4),
        "penalty_low_info_title": 1 if low_info else 0,
        "penalty_orphan": 1 if (not has_edges and low_info) else 0,
        "penalty_superseded_only": 1 if str(bead.get("status") or "") == "superseded" and not has_edges else 0,
    }
    return feats


def rerank_candidates(root: Path, query: str, candidates: list[dict]) -> dict:
    idx_file = root / ".beads" / "index.json"
    if not idx_file.exists():
        return {"ok": True, "results": candidates, "debug": []}

    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    beads = idx.get("beads") or {}
    q_tokens = _tokenize(query)

    out = []
    dbg = []
    for c in candidates:
        bid = str(c.get("bead_id") or "")
        bead = beads.get(bid) or {}
        f = _features_for(bead, q_tokens)
        structural_quality = (f["has_decision"] + f["has_evidence"] + f["has_outcome"]) / 3.0
        penalties = f["penalty_low_info_title"] + f["penalty_orphan"] + f["penalty_superseded_only"]
        fused = float(c.get("fused_score") or 0.0)

        score = (fused * W_FUSED) + (structural_quality * W_STRUCTURAL) + (float(f["query_term_coverage"]) * W_COVERAGE) - (penalties * W_PENALTY)
        score = max(0.0, min(1.0, float(score)))

        c2 = dict(c)
        c2["rerank_score"] = round(score, 4)
        c2["features"] = f
        out.append(c2)
        dbg.append({"bead_id": bid, "fused_score": fused, "rerank_score": c2["rerank_score"], "features": f})

    out = sorted(out, key=lambda r: str(r.get("bead_id") or ""))
    out = sorted(
        out,
        key=lambda r: (
            float(r.get("rerank_score") or 0.0),
            float(r.get("fused_score") or 0.0),
            float(r.get("sem_score") or 0.0),
            float(r.get("lex_score") or 0.0),
        ),
        reverse=True,
    )

    return {"ok": True, "results": out, "debug": dbg}
