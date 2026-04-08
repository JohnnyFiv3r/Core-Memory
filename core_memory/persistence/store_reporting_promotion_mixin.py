from __future__ import annotations

from typing import Optional


class StoreReportingPromotionMixin:
    def _infer_target_bead_for_question(self, question: str) -> Optional[dict]:
        """Infer target decision bead for a rationale question using token overlap."""
        from ..reporting.store_rationale import infer_target_bead_for_question

        return infer_target_bead_for_question(self, question)

    def evaluate_rationale_recall(self, question: str, answer: str, bead_id: Optional[str] = None) -> dict:
        """Deterministic 0/1/2 rationale recall scorer."""
        from ..reporting.store_rationale import evaluate_rationale_recall_for_store

        return evaluate_rationale_recall_for_store(self, question, answer, bead_id=bead_id)

    def metrics_report(self, since: str = "7d") -> dict:
        """Deterministic metrics aggregation delegated to reporting module."""
        from ..reporting.store_reporting import metrics_report_for_store

        return metrics_report_for_store(self, since=since)

    def append_autonomy_kpi(
        self,
        *,
        run_id: str,
        repeat_failure: bool = False,
        contradiction_resolved: bool = False,
        contradiction_latency_turns: int = 0,
        unjustified_flip: bool = False,
        constraint_violation: bool = False,
        wrong_transfer: bool = False,
        goal_carryover: bool = False,
    ) -> dict:
        """Append one autonomy KPI row (Phase 5 proof loop)."""
        from ..persistence.store_autonomy_ops import append_autonomy_kpi_for_store

        return append_autonomy_kpi_for_store(
            self,
            run_id=run_id,
            repeat_failure=repeat_failure,
            contradiction_resolved=contradiction_resolved,
            contradiction_latency_turns=contradiction_latency_turns,
            unjustified_flip=unjustified_flip,
            constraint_violation=constraint_violation,
            wrong_transfer=wrong_transfer,
            goal_carryover=goal_carryover,
        )

    def autonomy_report(self, since: str = "7d") -> dict:
        """Aggregate autonomy KPIs delegated to reporting module."""
        from ..reporting.store_reporting import autonomy_report_for_store

        return autonomy_report_for_store(self, since=since)

    def schema_quality_report(self, write_path: Optional[str] = None) -> dict:
        """Report required-field warnings and promotion gate blockers."""
        from ..reporting.store_reporting import schema_quality_report_for_store

        return schema_quality_report_for_store(self, write_path=write_path)

    def _reinforcement_signals(self, index: dict, bead: dict) -> dict:
        from ..persistence.store_autonomy_ops import reinforcement_signals_for_store

        return reinforcement_signals_for_store(self, index, bead)

    def _promotion_score(self, index: dict, bead: dict) -> tuple[float, dict]:
        from ..persistence.store_promotion_ops import promotion_score_for_store

        return promotion_score_for_store(self, index, bead)

    def _adaptive_promotion_threshold(self, index: dict) -> float:
        from ..persistence.store_promotion_ops import adaptive_promotion_threshold_for_store

        return adaptive_promotion_threshold_for_store(self, index)

    def _candidate_promotable(self, index: dict, bead: dict) -> tuple[bool, dict]:
        from ..persistence.store_promotion_ops import candidate_promotable_for_store

        return candidate_promotable_for_store(self, index, bead)

    def _candidate_recommendation_rows(self, index: dict, query_text: str = "") -> tuple[list[dict], float]:
        from ..persistence.store_promotion_ops import candidate_recommendation_rows_for_store

        return candidate_recommendation_rows_for_store(self, index, query_text=query_text)

    def promotion_slate(self, limit: int = 20, query_text: str = "") -> dict:
        """Build bounded candidate promotion slate with advisory recommendations."""
        from ..persistence.store_promotion_ops import promotion_slate_entry_for_store

        return promotion_slate_entry_for_store(self, limit=limit, query_text=query_text)

    def evaluate_candidates(
        self,
        limit: int = 50,
        query_text: str = "",
        auto_archive_hold: bool = False,
        min_age_hours: int = 12,
    ) -> dict:
        """Refresh advisory recommendation fields for candidates."""
        from ..persistence.store_promotion_ops import evaluate_candidates_entry_for_store

        return evaluate_candidates_entry_for_store(
            self,
            limit=limit,
            query_text=query_text,
            auto_archive_hold=auto_archive_hold,
            min_age_hours=min_age_hours,
        )

    def decide_promotion(
        self,
        *,
        bead_id: str,
        decision: str,
        reason: str = "",
        considerations: Optional[list[str]] = None,
    ) -> dict:
        """Apply agent-led promotion decision for a bead."""
        from ..persistence.store_promotion_ops import decide_promotion_entry_for_store

        return decide_promotion_entry_for_store(
            self,
            bead_id=bead_id,
            decision=decision,
            reason=reason,
            considerations=considerations,
        )

    def decide_promotion_bulk(self, decisions: list[dict]) -> dict:
        """Apply a bounded batch of agent promotion decisions."""
        from ..persistence.store_promotion_ops import decide_promotion_bulk_entry_for_store

        return decide_promotion_bulk_entry_for_store(self, decisions)

    def decide_session_promotion_states(self, *, session_id: str, visible_bead_ids: Optional[list[str]] = None, turn_id: str = "") -> dict:
        """Per-turn session decision pass: promoted|candidate|null for visible beads."""
        from ..persistence.store_promotion_ops import decide_session_promotion_states_entry_for_store

        return decide_session_promotion_states_entry_for_store(
            self,
            session_id=session_id,
            visible_bead_ids=visible_bead_ids,
            turn_id=turn_id,
        )

    def promotion_kpis(self, limit: int = 500) -> dict:
        """Report promotion decision volume, reasons, and rec-vs-decision alignment."""
        from ..persistence.store_promotion_ops import promotion_kpis_entry_for_store

        return promotion_kpis_entry_for_store(self, limit=limit)

    def rebalance_promotions(self, apply: bool = False) -> dict:
        """Phase B: score promoted beads and demote weakly-supported promotions."""
        from ..persistence.store_promotion_ops import rebalance_promotions_entry_for_store

        return rebalance_promotions_entry_for_store(self, apply=apply)


__all__ = ["StoreReportingPromotionMixin"]
