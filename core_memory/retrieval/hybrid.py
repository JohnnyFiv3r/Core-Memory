from __future__ import annotations

from pathlib import Path

from .types import Candidate
from .lexical import lexical_lookup
from core_memory.semantic_index import semantic_lookup


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    hi = max(scores)
    lo = min(scores)
    if hi <= lo:
        return [1.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def hybrid_lookup(root: Path, query: str, k: int = 8, w_sem: float = 0.55, w_lex: float = 0.45) -> dict:
    sem = semantic_lookup(root, query=query, k=max(10, int(k) * 3))
    lex = lexical_lookup(root, query=query, k=max(10, int(k) * 3))
    if not sem.get("ok") and not lex.get("ok"):
        return {"ok": False, "error": sem.get("error") or lex.get("error")}

    sem_rows = sem.get("results") or []
    lex_rows = lex.get("results") or []

    sem_norm = _normalize([float(r.get("score") or 0.0) for r in sem_rows])
    lex_norm = _normalize([float(r.get("score") or 0.0) for r in lex_rows])

    by_id: dict[str, Candidate] = {}

    for i, r in enumerate(sem_rows):
        bid = str(r.get("bead_id") or "")
        if not bid:
            continue
        c = by_id.get(bid) or Candidate(bead_id=bid)
        c.sem_score = float(sem_norm[i]) if i < len(sem_norm) else 0.0
        c.sem_rank = i + 1
        by_id[bid] = c

    for i, r in enumerate(lex_rows):
        bid = str(r.get("bead_id") or "")
        if not bid:
            continue
        c = by_id.get(bid) or Candidate(bead_id=bid)
        c.lex_score = float(lex_norm[i]) if i < len(lex_norm) else 0.0
        c.lex_rank = i + 1
        by_id[bid] = c

    out = []
    for c in by_id.values():
        c.fused_score = (w_sem * c.sem_score) + (w_lex * c.lex_score)
        out.append(c)

    out = sorted(out, key=lambda c: c.bead_id)
    out = sorted(out, key=lambda c: (c.fused_score, c.sem_score, c.lex_score), reverse=True)

    return {
        "ok": True,
        "query": query,
        "weights": {"semantic": w_sem, "lexical": w_lex},
        "semantic_backend": sem.get("backend"),
        "results": [c.to_dict() for c in out[: max(1, int(k))]],
    }
