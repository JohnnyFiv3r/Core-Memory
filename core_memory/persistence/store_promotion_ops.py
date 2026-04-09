from __future__ import annotations

from typing import Any, Optional

from core_memory.persistence.promotion_service import (
    decide_promotion_bulk_for_store,
    decide_promotion_for_store,
    decide_session_promotion_states_for_store,
    evaluate_candidates_for_store,
    promotion_kpis_for_store,
    promotion_slate_for_store,
    rebalance_promotions_for_store,
)
from core_memory.policy.promotion import (
    compute_adaptive_threshold,
    compute_promotion_score,
    get_recommendation_rows,
    is_candidate_promotable,
)


def promotion_score_for_store(store: Any, index: dict, bead: dict) -> tuple[float, dict]:
    return compute_promotion_score(index, bead)


def adaptive_promotion_threshold_for_store(store: Any, index: dict) -> float:
    return compute_adaptive_threshold(index)


def candidate_promotable_for_store(store: Any, index: dict, bead: dict) -> tuple[bool, dict]:
    return is_candidate_promotable(index, bead)


def candidate_recommendation_rows_for_store(store: Any, index: dict, query_text: str = "") -> tuple[list[dict], float]:
    return get_recommendation_rows(
        index,
        query_text=query_text,
        query_tokenize_fn=store._tokenize,
        query_expand_fn=store._expand_query_tokens,
    )


def promotion_slate_entry_for_store(store: Any, *, limit: int = 20, query_text: str = "") -> dict:
    return promotion_slate_for_store(store, limit=limit, query_text=query_text)


def evaluate_candidates_entry_for_store(
    store: Any,
    *,
    limit: int = 50,
    query_text: str = "",
    auto_archive_hold: bool = False,
    min_age_hours: int = 12,
) -> dict:
    return evaluate_candidates_for_store(
        store,
        limit=limit,
        query_text=query_text,
        auto_archive_hold=auto_archive_hold,
        min_age_hours=min_age_hours,
    )


def decide_promotion_entry_for_store(
    store: Any,
    *,
    bead_id: str,
    decision: str,
    reason: str = "",
    considerations: Optional[list[str]] = None,
) -> dict:
    return decide_promotion_for_store(
        store,
        bead_id=bead_id,
        decision=decision,
        reason=reason,
        considerations=considerations,
    )


def decide_promotion_bulk_entry_for_store(store: Any, decisions: list[dict]) -> dict:
    return decide_promotion_bulk_for_store(store, decisions)


def decide_session_promotion_states_entry_for_store(
    store: Any,
    *,
    session_id: str,
    visible_bead_ids: Optional[list[str]] = None,
    turn_id: str = "",
) -> dict:
    return decide_session_promotion_states_for_store(
        store,
        session_id=session_id,
        visible_bead_ids=visible_bead_ids,
        turn_id=turn_id,
    )


def promotion_kpis_entry_for_store(store: Any, *, limit: int = 500) -> dict:
    return promotion_kpis_for_store(store, limit=limit)


def rebalance_promotions_entry_for_store(store: Any, *, apply: bool = False) -> dict:
    return rebalance_promotions_for_store(store, apply=apply)


__all__ = [
    "promotion_score_for_store",
    "adaptive_promotion_threshold_for_store",
    "candidate_promotable_for_store",
    "candidate_recommendation_rows_for_store",
    "promotion_slate_entry_for_store",
    "evaluate_candidates_entry_for_store",
    "decide_promotion_entry_for_store",
    "decide_promotion_bulk_entry_for_store",
    "decide_session_promotion_states_entry_for_store",
    "promotion_kpis_entry_for_store",
    "rebalance_promotions_entry_for_store",
]
