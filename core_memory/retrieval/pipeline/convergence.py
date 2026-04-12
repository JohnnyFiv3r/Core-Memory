from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates


def normalize_intent_bucket(intent: str) -> str:
    x = str(intent or "").strip().lower()
    return x if x in {"causal", "remember", "what_changed", "when"} else "remember"


def run_hybrid_rerank_seeds(root: Path, *, query: str, intent: str, k: int) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {
            "ok": False,
            "error": "empty_query",
            "hybrid": {"ok": False, "error": "empty_query"},
            "rerank": {"ok": False, "error": "empty_query"},
            "results": [],
            "by_id": {},
            "warnings": ["empty_query"],
            "stages": {"hybrid_candidates": 0, "rerank_candidates": 0},
        }

    hk = max(20, int(k) * 4)
    h = hybrid_lookup(root, query=q, k=hk)
    if not h.get("ok"):
        return {
            "ok": False,
            "error": str(h.get("error") or "hybrid_failed"),
            "hybrid": h,
            "rerank": {"ok": False, "error": "hybrid_failed"},
            "results": [],
            "by_id": {},
            "warnings": ["hybrid_failed"],
            "stages": {"hybrid_candidates": 0, "rerank_candidates": 0},
        }

    bucket = normalize_intent_bucket(intent)
    rr = rerank_candidates(root, query=q, candidates=h.get("results") or [], intent_class=bucket)
    rr_rows = list(rr.get("results") or [])

    by_id: dict[str, dict[str, Any]] = {}
    for r in rr_rows:
        bid = str(r.get("bead_id") or "")
        if not bid:
            continue
        by_id[bid] = dict(r)

    return {
        "ok": True,
        "error": "",
        "hybrid": h,
        "rerank": rr,
        "results": rr_rows,
        "by_id": by_id,
        "warnings": [],
        "stages": {
            "hybrid_candidates": int(len(h.get("results") or [])),
            "rerank_candidates": int(len(rr_rows)),
        },
    }
