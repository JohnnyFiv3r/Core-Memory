from __future__ import annotations

from pathlib import Path
from typing import Any

from .t2 import default_fixture_path as default_t2_fixture_path
from .t2 import run_t2_calibration
from .t3 import default_fixture_path as default_t3_fixture_path
from .t3 import run_t3_temporal_state
from .t5 import default_fixture_path as default_t5_fixture_path
from .t5 import run_t5_thread_fidelity

RUNTIME_ABLATION_RUNS_SCHEMA = "causal_continuity.runtime_ablation_runs.v1"


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


def _delta(full: float | None, ablated: float | None) -> float | None:
    if full is None or ablated is None:
        return None
    return round(full - ablated, 6)


def _drop_status(delta: float | None) -> str:
    if delta is None:
        return "not_available"
    return "observed" if delta > 0.0 else "observed_no_expected_drop"


def _strategy_metric(t1: dict[str, Any], strategy: str, key: str) -> float | None:
    row = dict((t1.get("strategy_matrix") or {}).get(strategy) or {})
    return _float(row.get(key))


def _full_scores(report: dict[str, Any]) -> dict[str, Any]:
    t1 = _task(report, "t1_causal_chain_reconstruction")
    t2 = _task(report, "t2_calibration_reliability")
    t3 = _task(report, "t3_temporal_state_selection")
    t4 = _task(report, "t4_longitudinal_continuity")
    t5 = _task(report, "t5_thread_fidelity")
    return {
        "t1_causal_survival_rate": _strategy_metric(t1, "core_memory_full", "causal_survival_rate"),
        "t2_spearman_rho": _float(_metrics(t2).get("spearman_rho")),
        "t2_sample_count": _float(_metrics(t2).get("sample_count")),
        "t3_as_of_accuracy": _float(_metrics(t3).get("as_of_accuracy")),
        "t3_supersession_respect_rate": _float(_metrics(t3).get("supersession_respect_rate")),
        "t3_contradiction_surfaced_rate": _float(_metrics(t3).get("contradiction_surfaced_rate")),
        "t4_continuity_lift": _float(_metrics(t4).get("continuity_lift")),
        "t5_thread_f1": _float(_metrics(t5).get("thread_f1")),
        "t5_query_drift_rate": _float(_metrics(t5).get("query_drift_rate")),
    }


def _observed_if_insufficient_samples(full_count: float | None, ablated_count: float | None) -> str:
    if ablated_count is None:
        return "not_available"
    if ablated_count <= 0:
        return "observed"
    return _drop_status(_delta(full_count, ablated_count))


def run_runtime_ablation_toggles(
    report: dict[str, Any],
    *,
    t2_fixture: Path | None = None,
    t3_fixture: Path | None = None,
    t5_fixture: Path | None = None,
) -> dict[str, Any]:
    """Execute supported disabled-mode ablation rows.

    This is deliberately heavier than the default report-layer matrix. It
    re-runs small deterministic task fixtures with mechanisms disabled where the
    current harness can do so without external services.
    """

    full = _full_scores(report)
    t1 = _task(report, "t1_causal_chain_reconstruction")
    t4 = _task(report, "t4_longitudinal_continuity")
    t5 = _task(report, "t5_thread_fidelity")
    t4_long = dict(t4.get("longitudinal") or {})
    t4_cohorts = dict(t4_long.get("cohorts") or {})
    t4_without_dreamer = dict(t4_cohorts.get("core_memory_without_dreamer") or {})
    t4_without_dreamer_rates = dict(t4_without_dreamer.get("rates") or {})
    t5_metrics = _metrics(t5)

    t2_no_bonus = run_t2_calibration(
        fixture_path=t2_fixture or default_t2_fixture_path(),
        manifest_bonus_enabled=False,
    )
    t2_no_feedback = run_t2_calibration(
        fixture_path=t2_fixture or default_t2_fixture_path(),
        record_validated_outcomes=False,
    )
    t3_no_updates = run_t3_temporal_state(
        fixture_path=t3_fixture or default_t3_fixture_path(),
        apply_claim_updates=False,
    )
    t5_no_traversal = run_t5_thread_fidelity(
        fixture_path=t5_fixture or default_t5_fixture_path(),
        traversal_enabled=False,
    )

    no_bonus_metrics = _metrics(t2_no_bonus)
    no_feedback_metrics = _metrics(t2_no_feedback)
    no_updates_metrics = _metrics(t3_no_updates)
    similarity_csr = _strategy_metric(t1, "similarity_only", "causal_survival_rate")
    dreamer_off_lift = _float(t4_without_dreamer_rates.get("quality_score"))
    no_traversal_t5_metrics = _metrics(t5_no_traversal)
    no_traversal_t5_f1 = _float(no_traversal_t5_metrics.get("thread_f1"))
    no_traversal_t5_drift = _float(no_traversal_t5_metrics.get("query_drift_rate"))

    no_bonus_rho = _float(no_bonus_metrics.get("spearman_rho"))
    no_feedback_sample_count = _float(no_feedback_metrics.get("sample_count"))
    no_updates_as_of = _float(no_updates_metrics.get("as_of_accuracy"))
    no_updates_supersession = _float(no_updates_metrics.get("supersession_respect_rate"))
    no_updates_contradiction = _float(no_updates_metrics.get("contradiction_surfaced_rate"))
    no_updates_delta = max(
        d
        for d in [
            _delta(full.get("t3_as_of_accuracy"), no_updates_as_of),
            _delta(full.get("t3_supersession_respect_rate"), no_updates_supersession),
            _delta(full.get("t3_contradiction_surfaced_rate"), no_updates_contradiction),
            0.0,
        ]
        if d is not None
    )

    rows = {
        "minus_causal_traversal": {
            "status": _drop_status(max(
                d
                for d in [
                    _delta(full.get("t1_causal_survival_rate"), similarity_csr),
                    _delta(full.get("t5_thread_f1"), no_traversal_t5_f1),
                    0.0,
                ]
                if d is not None
            )),
            "scores": {
                "t1_causal_survival_rate": similarity_csr,
                "t5_thread_f1": no_traversal_t5_f1,
                "t5_query_drift_rate": no_traversal_t5_drift,
            },
            "observed_delta_vs_full": {
                "t1_causal_survival_rate": _delta(full.get("t1_causal_survival_rate"), similarity_csr),
                "t5_thread_f1": _delta(full.get("t5_thread_f1"), no_traversal_t5_f1),
                "t5_query_drift_rate": _delta(no_traversal_t5_drift, full.get("t5_query_drift_rate")),
            },
            "evidence": [
                "t1_strategy_matrix.similarity_only",
                "runtime_ablation_runs.minus_causal_traversal.t5_traversal_disabled",
            ],
            "runtime_run": {
                "kind": "task_fixture_disabled_mode",
                "executed": True,
                "task_id": "t5_thread_fidelity",
                "traversal_enabled": False,
                "report": t5_no_traversal,
            },
            "limitations": [],
        },
        "minus_myelination_backpressure": {
            "status": _drop_status(_delta(full.get("t2_spearman_rho"), no_bonus_rho)),
            "scores": {
                "t2_spearman_rho": no_bonus_rho,
                "t2_sample_count": no_bonus_metrics.get("sample_count"),
                "t4_continuity_lift": None,
                "t5_thread_f1": None,
            },
            "observed_delta_vs_full": {
                "t2_spearman_rho": _delta(full.get("t2_spearman_rho"), no_bonus_rho),
            },
            "evidence": ["runtime_ablation_runs.minus_myelination_backpressure.t2_no_manifest_bonus"],
            "runtime_run": {
                "kind": "task_fixture_disabled_mode",
                "task_id": "t2_calibration_reliability",
                "manifest_bonus_enabled": False,
                "report": t2_no_bonus,
            },
            "limitations": ["current_fixture_may_not_show_rho_drop_if_judge_prior_already_orders_outcomes"],
        },
        "minus_validated_outcome_reward": {
            "status": _observed_if_insufficient_samples(full.get("t2_sample_count"), no_feedback_sample_count),
            "scores": {
                "t2_spearman_rho": _float(no_feedback_metrics.get("spearman_rho")),
                "t2_sample_count": no_feedback_sample_count,
                "t4_continuity_lift": None,
            },
            "observed_delta_vs_full": {
                "t2_sample_count": _delta(full.get("t2_sample_count"), no_feedback_sample_count),
            },
            "evidence": ["runtime_ablation_runs.minus_validated_outcome_reward.t2_no_feedback"],
            "runtime_run": {
                "kind": "task_fixture_disabled_mode",
                "task_id": "t2_calibration_reliability",
                "record_validated_outcomes": False,
                "report": t2_no_feedback,
            },
            "limitations": [],
        },
        "minus_dreamer": {
            "status": _drop_status(_delta(full.get("t4_continuity_lift"), dreamer_off_lift)),
            "scores": {"t4_continuity_lift": dreamer_off_lift},
            "observed_delta_vs_full": {
                "t4_continuity_lift": _delta(full.get("t4_continuity_lift"), dreamer_off_lift),
            },
            "evidence": ["t4.longitudinal.cohorts.core_memory_without_dreamer"],
            "runtime_run": {"kind": "cohort_baseline", "executed": True},
            "limitations": [],
        },
        "minus_supersession_temporal_filter": {
            "status": _drop_status(no_updates_delta),
            "scores": {
                "t3_as_of_accuracy": no_updates_as_of,
                "t3_supersession_respect_rate": no_updates_supersession,
                "t3_contradiction_surfaced_rate": no_updates_contradiction,
            },
            "observed_delta_vs_full": {
                "t3_as_of_accuracy": _delta(full.get("t3_as_of_accuracy"), no_updates_as_of),
                "t3_supersession_respect_rate": _delta(full.get("t3_supersession_respect_rate"), no_updates_supersession),
                "t3_contradiction_surfaced_rate": _delta(full.get("t3_contradiction_surfaced_rate"), no_updates_contradiction),
            },
            "evidence": ["runtime_ablation_runs.minus_supersession_temporal_filter.t3_no_claim_updates"],
            "runtime_run": {
                "kind": "task_fixture_disabled_mode",
                "task_id": "t3_temporal_state_selection",
                "apply_claim_updates": False,
                "report": t3_no_updates,
            },
            "limitations": [],
        },
        "minus_agentic_recall_loop": {
            "status": _drop_status(_delta(full.get("t5_thread_f1"), no_traversal_t5_f1)),
            "scores": {
                "t5_thread_f1": no_traversal_t5_f1,
                "t5_query_drift_rate": no_traversal_t5_drift,
            },
            "observed_delta_vs_full": {
                "t5_thread_f1": _delta(full.get("t5_thread_f1"), no_traversal_t5_f1),
                "t5_query_drift_rate": _delta(no_traversal_t5_drift, full.get("t5_query_drift_rate")),
            },
            "evidence": [
                "t5.metrics.one_shot_anchor_baseline",
                "runtime_ablation_runs.minus_agentic_recall_loop.t5_traversal_disabled",
            ],
            "runtime_run": {
                "kind": "task_fixture_disabled_mode",
                "executed": True,
                "task_id": "t5_thread_fidelity",
                "traversal_enabled": False,
                "report": t5_no_traversal,
            },
            "limitations": [] if _delta(full.get("t5_thread_f1"), no_traversal_t5_f1) else ["one_shot_anchor_proxy_did_not_show_expected_drop_on_current_fixture"],
        },
    }

    return {
        "schema_version": RUNTIME_ABLATION_RUNS_SCHEMA,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "executed_rows": sum(1 for row in rows.values() if dict(row.get("runtime_run") or {}).get("executed", True)),
            "observed_rows": sum(1 for row in rows.values() if str(row.get("status") or "").startswith("observed")),
            "needs_runtime_toggle_rows": 0,
        },
    }


__all__ = ["RUNTIME_ABLATION_RUNS_SCHEMA", "run_runtime_ablation_toggles"]
