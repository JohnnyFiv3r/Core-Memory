from __future__ import annotations

import json
from pathlib import Path

from .types import Candidate
from .lexical import lexical_lookup
from core_memory.semantic_index import semantic_lookup
from core_memory.policy.incidents import matched_incident_ids, incident_match_strength
from .config import INCIDENT_FLOOR, NORM_EPS
from .query_norm import resolve_query_anchors


def _normalize(scores: list[float]) -> tuple[list[float], str]:
    if not scores:
        return [], "none"
    hi = max(scores)
    lo = min(scores)
    if (hi - lo) > NORM_EPS:
        return [((s - lo) / (hi - lo)) for s in scores], "minmax"
    k = len(scores)
    if k <= 1:
        return [1.0], "rank"
    return [1.0 - (i / (k - 1)) for i in range(k)], "rank"


def hybrid_lookup(root: Path, query: str, k: int = 8, w_sem: float = 0.55, w_lex: float = 0.45) -> dict:
    anchor_meta = resolve_query_anchors(query, root)
    retrieval_query = str(anchor_meta.get("expanded_query") or query or "").strip()
    if not retrieval_query:
        retrieval_query = str(query or "")

    sem = semantic_lookup(root, query=retrieval_query, k=max(10, int(k) * 3))
    lex = lexical_lookup(root, query=retrieval_query, k=max(10, int(k) * 3))
    if not sem.get("ok") and not lex.get("ok"):
        return {"ok": False, "error": sem.get("error") or lex.get("error")}

    sem_rows = sem.get("results") or []
    lex_rows = lex.get("results") or []

    sem_norm, sem_mode = _normalize([float(r.get("score") or 0.0) for r in sem_rows])
    lex_norm, lex_mode = _normalize([float(r.get("score") or 0.0) for r in lex_rows])

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

    incident_matches = matched_incident_ids(query, root)
    topic_matches = {str(x.get("topic_key") or "") for x in (anchor_meta.get("matched_topics") or []) if x.get("topic_key")}
    incident_by_bead = {}
    tags_by_bead = {}
    idx_file = root / ".beads" / "index.json"
    if idx_file.exists():
        try:
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for bid, b in (idx.get("beads") or {}).items():
                incident_by_bead[str(bid)] = str((b or {}).get("incident_id") or "")
                tags_by_bead[str(bid)] = [str(t) for t in ((b or {}).get("tags") or [])]
        except Exception:
            incident_by_bead = {}
            tags_by_bead = {}

    out = []
    for c in by_id.values():
        c.fused_score = (w_sem * c.sem_score) + (w_lex * c.lex_score)
        iid = incident_by_bead.get(c.bead_id, "")
        if iid and iid in incident_matches and c.fused_score >= INCIDENT_FLOOR:
            c.fused_score += 0.12 * incident_match_strength(query, iid, root)
        if topic_matches:
            bt = set(tags_by_bead.get(c.bead_id, []))
            if bt.intersection(topic_matches):
                c.fused_score += 0.08
        out.append(c)

    out = sorted(
        out,
        key=lambda c: (
            -float(c.fused_score),
            -float(c.sem_score),
            -float(c.lex_score),
            str(c.bead_id),
        ),
    )

    ranked = []
    for rank, c in enumerate(out[: max(1, int(k))], start=1):
        row = c.to_dict()
        row["rank"] = rank
        row["tie_break_policy"] = "fused>sem>lex>bead_id"
        ranked.append(row)

    return {
        "ok": True,
        "query": query,
        "retrieval_query": retrieval_query,
        "weights": {"semantic": w_sem, "lexical": w_lex},
        "semantic_backend": sem.get("backend"),
        "matched_incidents": incident_matches,
        "matched_topics": sorted(topic_matches),
        "normalization": {"semantic": sem_mode, "lexical": lex_mode},
        "results": ranked,
    }
