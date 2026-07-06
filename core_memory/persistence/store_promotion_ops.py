from __future__ import annotations

from typing import Any

from core_memory.schema.promotion import (
    compute_adaptive_threshold,
    compute_promotion_score,
    get_recommendation_rows,
    is_candidate_promotable,
)


def promotion_score_for_store(index: dict, bead: dict) -> tuple[float, dict]:
    return compute_promotion_score(index, bead)


def adaptive_promotion_threshold_for_store(index: dict) -> float:
    return compute_adaptive_threshold(index)


def candidate_promotable_for_store(index: dict, bead: dict) -> tuple[bool, dict]:
    return is_candidate_promotable(index, bead)


def candidate_recommendation_rows_for_store(store: Any, index: dict, query_text: str = "") -> tuple[list[dict], float]:
    return get_recommendation_rows(
        index,
        query_text=query_text,
        query_tokenize_fn=store._tokenize,
        query_expand_fn=store._expand_query_tokens,
    )


__all__ = [
    "promotion_score_for_store",
    "adaptive_promotion_threshold_for_store",
    "candidate_promotable_for_store",
    "candidate_recommendation_rows_for_store",
]
