from __future__ import annotations

from typing import Any

ABLATION_REPORT_SCHEMA = "causal_continuity.ablation_matrix.v1"


def _task(report: dict[str, Any], name: str) -> dict[str, Any]:
    return dict((report.get("tasks") or {}).get(name) or {})


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("metrics") or {})


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strategy_metric(t1: dict[str, Any], strategy: str, key: str) -> float | None:
    matrix = dict(t1.get("strategy_matrix") or {})
    row = dict(matrix.get(strategy) or {})
    return _float(row.get(key))


def _delta(full: float | None, ablated: float | None) -> float | None:
    if full is None or ablated is None:
        return None
    return round(full - ablated, 6)


def _drop_status(delta: float | None) -> str:
    if delta is None:
        return "not_available"
    return "observed" if delta > 0.0 else "observed_no_expected_drop"


def _full_scores(report: dict[str, Any]) -> dict[str, Any]:
    t1 = _task(report, "t1_causal_chain_reconstruction")
    t2 = _task(report, "t2_calibration_reliability")
    t3 = _task(report, "t3_temporal_state_selection")
    t4 = _task(report, "t4_longitudinal_continuity")
    t5 = _task(report, "t5_thread_fidelity")
    return {
        "t1_causal_survival_rate": _strategy_metric(t1, "core_memory_full", "causal_survival_rate"),
        "t2_calibration_pass": bool(t2.get("pass")) if t2 else None,
        "t2_spearman_rho": _float(_metrics(t2).get("spearman_rho")),
        "t3_as_of_accuracy": _float(_metrics(t3).get("as_of_accuracy")),
        "t3_supersession_respect_rate": _float(_metrics(t3).get("supersession_respect_rate")),
        "t4_continuity_lift": _float(_metrics(t4).get("continuity_lift")),
        "t5_thread_f1": _float(_metrics(t5).get("thread_f1")),
        "t5_query_drift_rate": _float(_metrics(t5).get("query_drift_rate")),
    }


def _apply_runtime_runs(rows: list[dict[str, Any]], runtime_runs: dict[str, Any]) -> None:
    runtime_rows = dict(runtime_runs.get("rows") or {})
    for row in rows:
        row_id = str(row.get("id") or "")
        overlay = dict(runtime_rows.get(row_id) or {})
        if not overlay:
            continue
        row["status"] = str(overlay.get("status") or row.get("status") or "")
        row["scores"] = dict(overlay.get("scores") or row.get("scores") or {})
        row["observed_delta_vs_full"] = dict(overlay.get("observed_delta_vs_full") or row.get("observed_delta_vs_full") or {})
        row["evidence"] = list(overlay.get("evidence") or row.get("evidence") or [])
        row["limitations"] = list(overlay.get("limitations") or row.get("limitations") or [])
        row["runtime_run"] = dict(overlay.get("runtime_run") or {})


def build_ablation_matrix(report: dict[str, Any], *, runtime_runs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the PRD section 7 mechanism-ownership matrix from suite outputs.

    This is deliberately a report-layer matrix: it records which mechanism rows
    are observed from current harness telemetry and which still need dedicated
    runtime toggles. That keeps the paper build honest while making the
    remaining instrumentation work explicit.
    """
    t1 = _task(report, "t1_causal_chain_reconstruction")
    t4 = _task(report, "t4_longitudinal_continuity")
    t5 = _task(report, "t5_thread_fidelity")
    full = _full_scores(report)
    t4_long = dict(t4.get("longitudinal") or {})
    t4_cohorts = dict(t4_long.get("cohorts") or {})
    t4_core_without = dict(t4_cohorts.get("core_memory_without_dreamer") or {})
    t4_core_without_rates = dict(t4_core_without.get("rates") or {})
    t5_metrics = _metrics(t5)

    similarity_csr = _strategy_metric(t1, "similarity_only", "causal_survival_rate")
    dreamer_off_lift = _float(t4_core_without_rates.get("quality_score"))
    one_shot_t5_f1 = _float(t5_metrics.get("one_shot_thread_f1"))
    one_shot_drift = _float(t5_metrics.get("one_shot_query_drift_rate"))
    causal_traversal_delta = _delta(full.get("t1_causal_survival_rate"), similarity_csr)
    dreamer_delta = _delta(full.get("t4_continuity_lift"), dreamer_off_lift)
    agentic_delta = _delta(full.get("t5_thread_f1"), one_shot_t5_f1)

    rows: list[dict[str, Any]] = [
        {
            "id": "core_memory_full",
            "label": "Core Memory (full)",
            "status": "observed",
            "mechanism_removed": None,
            "scores": full,
            "expected_drop": {},
            "observed_delta_vs_full": {},
            "evidence": ["suite_task_headlines"],
        },
        {
            "id": "minus_causal_traversal",
            "label": "- causal traversal (similarity only)",
            "status": _drop_status(causal_traversal_delta),
            "mechanism_removed": "causal_traversal",
            "scores": {
                "t1_causal_survival_rate": similarity_csr,
                "t5_thread_f1": None,
            },
            "expected_drop": {
                "t1_causal_survival_rate": "collapse",
                "t5_thread_f1": "collapse",
            },
            "observed_delta_vs_full": {
                "t1_causal_survival_rate": causal_traversal_delta,
            },
            "evidence": ["t1_strategy_matrix.similarity_only"],
            "limitations": ["t5_no_traversal_runtime_toggle_not_yet_instrumented"],
        },
        {
            "id": "minus_myelination_backpressure",
            "label": "- myelination backpressure (bonus_by_edge_key off)",
            "status": "needs_runtime_toggle",
            "mechanism_removed": "myelination_backpressure",
            "scores": {
                "t2_spearman_rho": None,
                "t4_continuity_lift": None,
                "t5_thread_f1": None,
            },
            "expected_drop": {
                "t2_spearman_rho": "flat_or_lower",
                "t4_continuity_lift": "drop",
                "t5_thread_f1": "drop",
            },
            "observed_delta_vs_full": {},
            "evidence": [],
            "limitations": ["requires_manifest_bonus_toggle_run"],
        },
        {
            "id": "minus_validated_outcome_reward",
            "label": "- validated-outcome reward",
            "status": "needs_runtime_toggle",
            "mechanism_removed": "validated_outcome_reward",
            "scores": {
                "t2_spearman_rho": None,
                "t4_continuity_lift": None,
            },
            "expected_drop": {
                "t2_spearman_rho": "lower_or_insufficient_data",
                "t4_continuity_lift": "drop",
            },
            "observed_delta_vs_full": {},
            "evidence": [],
            "limitations": ["requires_feedback_reward_disabled_run"],
        },
        {
            "id": "minus_dreamer",
            "label": "- dreamer",
            "status": _drop_status(dreamer_delta),
            "mechanism_removed": "dreamer",
            "scores": {
                "t4_continuity_lift": dreamer_off_lift,
            },
            "expected_drop": {
                "t4_continuity_lift": "lift_to_zero_or_lower",
            },
            "observed_delta_vs_full": {
                "t4_continuity_lift": dreamer_delta,
            },
            "evidence": ["t4.longitudinal.cohorts.core_memory_without_dreamer"],
        },
        {
            "id": "minus_supersession_temporal_filter",
            "label": "- supersession/temporal filter",
            "status": "needs_runtime_toggle",
            "mechanism_removed": "supersession_temporal_filter",
            "scores": {
                "t3_as_of_accuracy": None,
                "t3_supersession_respect_rate": None,
            },
            "expected_drop": {
                "t3_as_of_accuracy": "drop",
                "t3_supersession_respect_rate": "drop",
            },
            "observed_delta_vs_full": {},
            "evidence": [],
            "limitations": ["requires_claim_resolver_temporal_filter_disabled_run"],
        },
        {
            "id": "minus_agentic_recall_loop",
            "label": "- agentic recall loop (one-shot recall)",
            "status": _drop_status(agentic_delta),
            "mechanism_removed": "agentic_recall_loop",
            "scores": {
                "t5_thread_f1": one_shot_t5_f1,
                "t5_query_drift_rate": one_shot_drift,
            },
            "expected_drop": {
                "t5_thread_f1": "drop",
                "t5_query_drift_rate": "increase",
            },
            "observed_delta_vs_full": {
                "t5_thread_f1": agentic_delta,
                "t5_query_drift_rate": _delta(one_shot_drift, full.get("t5_query_drift_rate")),
            },
            "evidence": ["t5.metrics.one_shot_anchor_baseline"],
            "limitations": [] if agentic_delta and agentic_delta > 0.0 else ["one_shot_anchor_proxy_did_not_show_expected_drop_on_current_fixture"],
        },
    ]

    runtime_runs_payload = dict(runtime_runs or {})
    if runtime_runs_payload:
        _apply_runtime_runs(rows, runtime_runs_payload)

    statuses = [str(row.get("status") or "") for row in rows]
    has_runtime_runs = bool(runtime_runs_payload)
    return {
        "schema_version": ABLATION_REPORT_SCHEMA,
        "methodology": {
            "kind": "runtime_ablation_matrix" if has_runtime_runs else "suite_report_ablation_matrix",
            "mechanism_rows": len(rows),
            "notes": [
                "full row is the current suite output",
                "runtime rows are executed disabled-mode task fixtures" if has_runtime_runs else "observed rows come from existing strategy/cohort/baseline telemetry",
                "remaining needs_runtime_toggle rows are explicit instrumentation gaps",
            ],
        },
        "faithfulness": dict(report.get("faithfulness") or {}),
        "runtime_ablation_runs": runtime_runs_payload,
        "rows": rows,
        "coverage": {
            "observed_rows": sum(1 for status in statuses if status.startswith("observed")),
            "observed_expected_drop_rows": sum(1 for status in statuses if status == "observed"),
            "observed_no_expected_drop_rows": sum(1 for status in statuses if status == "observed_no_expected_drop"),
            "needs_runtime_toggle_rows": sum(1 for status in statuses if status == "needs_runtime_toggle"),
            "not_available_rows": sum(1 for status in statuses if status == "not_available"),
            "all_rows_observed": all(status.startswith("observed") for status in statuses),
            "faithfulness_clean": bool((report.get("faithfulness") or {}).get("is_faithful", True)),
        },
    }


__all__ = ["ABLATION_REPORT_SCHEMA", "build_ablation_matrix"]
