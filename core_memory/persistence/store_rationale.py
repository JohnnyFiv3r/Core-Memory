from __future__ import annotations

import re
from typing import Any, Optional


def infer_target_bead_for_question(store: Any, question: str) -> Optional[dict]:
    """Infer target decision bead for a rationale question using token overlap."""
    idx = store._read_json(store.beads_dir / "index.json")
    q_tokens = store._title_tokens(question or "")
    best = None
    best_score = 0
    for bead in idx.get("beads", {}).values():
        if bead.get("type") != "decision":
            continue
        b_tokens = store._title_tokens(bead.get("title", ""))
        score = len(q_tokens.intersection(b_tokens))
        if score > best_score:
            best_score = score
            best = bead
    return best


def evaluate_rationale_recall_for_store(
    store: Any,
    question: str,
    answer: str,
    bead_id: Optional[str] = None,
) -> dict:
    """Deterministic 0/1/2 rationale recall scorer.

    0 = incorrect/no grounding
    1 = partial (either citation or rationale overlap)
    2 = correct bead citation + rationale overlap
    """
    idx = store._read_json(store.beads_dir / "index.json")
    target = None
    if bead_id:
        target = (idx.get("beads") or {}).get(bead_id)
    if target is None:
        target = infer_target_bead_for_question(store, question)

    if not target:
        return {
            "score": 0,
            "target_bead_id": None,
            "reason": "no_target_bead",
            "cited_ids": [],
            "overlap_tokens": [],
        }

    target_id = target.get("id")
    cited_ids = re.findall(r"bead-[A-Za-z0-9]{8,}", answer or "")
    cited_match = target_id in cited_ids

    rationale_text = " ".join(target.get("because", []))
    rationale_text += " " + (target.get("mechanism") or "")
    rationale_text += " " + " ".join(target.get("summary", []))

    answer_tokens = store._tokenize(answer or "")
    rationale_tokens = store._tokenize(rationale_text)
    overlap = sorted(answer_tokens.intersection(rationale_tokens))

    score = 0
    if cited_match and len(overlap) >= 2:
        score = 2
    elif cited_match or len(overlap) >= 2:
        score = 1

    return {
        "score": score,
        "target_bead_id": target_id,
        "reason": "ok" if score > 0 else "insufficient_grounding",
        "cited_ids": cited_ids,
        "overlap_tokens": overlap[:20],
    }


__all__ = ["infer_target_bead_for_question", "evaluate_rationale_recall_for_store"]
