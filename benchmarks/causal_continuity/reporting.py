from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _extract_t1_headlines(t1_report: dict[str, Any]) -> dict[str, Any]:
    matrix = dict(t1_report.get("strategy_matrix") or {})
    return {
        "causal_survival_rate_by_strategy": {
            name: float(row.get("causal_survival_rate") or 0.0)
            for name, row in matrix.items()
        },
        "root_cause_accuracy_by_strategy": {
            name: float(row.get("root_cause_accuracy") or 0.0)
            for name, row in matrix.items()
        },
        "edge_f1_by_strategy": {
            name: float(row.get("edge_f1_mean") or 0.0)
            for name, row in matrix.items()
        },
        "status_by_strategy": {
            name: str(row.get("status") or "completed")
            for name, row in matrix.items()
        },
        "availability_by_strategy": {
            name: str(row.get("availability") or "")
            for name, row in matrix.items()
        },
    }


def _extract_t2_headlines(t2_report: dict[str, Any] | None) -> dict[str, Any]:
    if not t2_report:
        return {}
    metrics = dict(t2_report.get("metrics") or {})
    return {
        "pass": bool(t2_report.get("pass")),
        "spearman_rho": metrics.get("spearman_rho"),
        "expected_calibration_error": metrics.get("expected_calibration_error"),
        "brier_score": metrics.get("brier_score"),
        "high_band_usefulness_rate": metrics.get("high_band_usefulness_rate"),
        "auto_mode_gate": metrics.get("auto_mode_gate"),
        "sample_count": int(metrics.get("sample_count") or 0),
    }


def _extract_t3_headlines(t3_report: dict[str, Any] | None) -> dict[str, Any]:
    if not t3_report:
        return {}
    metrics = dict(t3_report.get("metrics") or {})
    return {
        "pass": bool(t3_report.get("pass")),
        "correct_state_selection_rate": metrics.get("correct_state_selection_rate"),
        "as_of_accuracy": metrics.get("as_of_accuracy"),
        "supersession_respect_rate": metrics.get("supersession_respect_rate"),
        "contradiction_surfaced_rate": metrics.get("contradiction_surfaced_rate"),
        "case_count": int(metrics.get("case_count") or 0),
    }


def _extract_t4_headlines(t4_report: dict[str, Any] | None) -> dict[str, Any]:
    if not t4_report:
        return {}
    metrics = dict(t4_report.get("metrics") or {})
    return {
        "pass": bool(t4_report.get("pass")),
        "continuity_lift": metrics.get("continuity_lift"),
        "self_model_drift_score": metrics.get("self_model_drift_score"),
        "self_model_drift_status": metrics.get("self_model_drift_status"),
        "goal_thread_persistence_rate": metrics.get("goal_thread_persistence_rate"),
        "accepted_applied_structural_candidates": int(metrics.get("accepted_applied_structural_candidates") or 0),
    }


def _extract_t5_headlines(t5_report: dict[str, Any] | None) -> dict[str, Any]:
    if not t5_report:
        return {}
    metrics = dict(t5_report.get("metrics") or {})
    return {
        "pass": bool(t5_report.get("pass")),
        "thread_precision": metrics.get("thread_precision"),
        "thread_recall": metrics.get("thread_recall"),
        "thread_f1": metrics.get("thread_f1"),
        "answerability": metrics.get("answerability"),
        "query_drift_rate": metrics.get("query_drift_rate"),
        "case_count": int(metrics.get("case_count") or 0),
    }


def _faithfulness_by_scope(
    *,
    t1_report: dict[str, Any],
    t2_report: dict[str, Any] | None,
    t3_report: dict[str, Any] | None,
    t4_report: dict[str, Any] | None,
    t5_report: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, report in sorted((t1_report.get("strategy_reports") or {}).items()):
        meta = dict(report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out[f"t1:{name}"] = flags
    if t2_report:
        meta = dict(t2_report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out["t2:calibration_reliability"] = flags
    if t3_report:
        meta = dict(t3_report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out["t3:temporal_state_selection"] = flags
    if t4_report:
        meta = dict(t4_report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out["t4:longitudinal_continuity"] = flags
    if t5_report:
        meta = dict(t5_report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out["t5:thread_fidelity"] = flags
    return out


def build_suite_report(
    *,
    metadata: dict[str, Any],
    t1_report: dict[str, Any] | None = None,
    t2_report: dict[str, Any] | None = None,
    t3_report: dict[str, Any] | None = None,
    t4_report: dict[str, Any] | None = None,
    t5_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t1 = dict(t1_report or {})
    by_scope = _faithfulness_by_scope(
        t1_report=t1,
        t2_report=t2_report,
        t3_report=t3_report,
        t4_report=t4_report,
        t5_report=t5_report,
    )
    is_faithful = all(bool(v.get("is_faithful")) for v in by_scope.values()) if by_scope else True

    warnings: list[str] = []
    for report in (t1.get("strategy_reports") or {}).values():
        warnings.extend(str(w) for w in (report.get("warnings") or []))

    tasks: dict[str, Any] = {}
    headlines: dict[str, Any] = {}
    if t1:
        tasks["t1_causal_chain_reconstruction"] = t1
        headlines["t1_causal_chain_reconstruction"] = _extract_t1_headlines(t1)
    if t2_report:
        tasks["t2_calibration_reliability"] = t2_report
        headlines["t2_calibration_reliability"] = _extract_t2_headlines(t2_report)
    if t3_report:
        tasks["t3_temporal_state_selection"] = t3_report
        headlines["t3_temporal_state_selection"] = _extract_t3_headlines(t3_report)
    if t4_report:
        tasks["t4_longitudinal_continuity"] = t4_report
        headlines["t4_longitudinal_continuity"] = _extract_t4_headlines(t4_report)
    if t5_report:
        tasks["t5_thread_fidelity"] = t5_report
        headlines["t5_thread_fidelity"] = _extract_t5_headlines(t5_report)

    return {
        "schema_version": "causal_continuity_report.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "faithfulness": {
            "is_faithful": bool(is_faithful),
            "by_scope": by_scope,
            "by_strategy": {
                k.removeprefix("t1:"): v
                for k, v in by_scope.items()
                if k.startswith("t1:")
            },
        },
        "headlines": headlines,
        "tasks": tasks,
        "warnings": sorted(set(warnings)),
    }


def render_summary(report: dict[str, Any]) -> str:
    meta = dict(report.get("metadata") or {})
    faith = dict(report.get("faithfulness") or {})
    t1 = dict((report.get("tasks") or {}).get("t1_causal_chain_reconstruction") or {})
    t2 = dict((report.get("tasks") or {}).get("t2_calibration_reliability") or {})
    t3 = dict((report.get("tasks") or {}).get("t3_temporal_state_selection") or {})
    t4 = dict((report.get("tasks") or {}).get("t4_longitudinal_continuity") or {})
    t5 = dict((report.get("tasks") or {}).get("t5_thread_fidelity") or {})
    matrix = dict(t1.get("strategy_matrix") or {})

    lines = [
        "Causal-Continuity Evaluation Suite",
        f"- suite: {meta.get('suite', 'causal_continuity')}  task_count: {meta.get('task_count', 1)}",
        f"- faithful: {str(bool(faith.get('is_faithful', True))).lower()}",
    ]

    if t1:
        lines.append("- T1 causal-chain reconstruction:")
        for name, row in sorted(matrix.items()):
            status = str(row.get("status") or "completed")
            status_suffix = "" if status == "completed" else f"  status={status}"
            lines.append(
                "  - "
                f"{name}: CSR={float(row.get('causal_survival_rate') or 0.0):.4f}  "
                f"root={float(row.get('root_cause_accuracy') or 0.0):.4f}  "
                f"edge_f1={float(row.get('edge_f1_mean') or 0.0):.4f}  "
                f"cases={int(row.get('cases') or 0)}"
                f"{status_suffix}"
            )
    if t2:
        metrics = dict(t2.get("metrics") or {})
        lines.append("- T2 calibration reliability:")
        lines.append(
            "  - "
            f"pass={str(bool(t2.get('pass'))).lower()}  "
            f"rho={float(metrics.get('spearman_rho') or 0.0):.4f}  "
            f"ece={float(metrics.get('expected_calibration_error') or 0.0):.4f}  "
            f"brier={float(metrics.get('brier_score') or 0.0):.4f}  "
            f"high_band={float(metrics.get('high_band_usefulness_rate') or 0.0):.4f}  "
            f"samples={int(metrics.get('sample_count') or 0)}"
        )
    if t3:
        metrics = dict(t3.get("metrics") or {})
        lines.append("- T3 temporal state selection:")
        lines.append(
            "  - "
            f"pass={str(bool(t3.get('pass'))).lower()}  "
            f"state={float(metrics.get('correct_state_selection_rate') or 0.0):.4f}  "
            f"as_of={float(metrics.get('as_of_accuracy') or 0.0):.4f}  "
            f"supersession={float(metrics.get('supersession_respect_rate') or 0.0):.4f}  "
            f"conflict={float(metrics.get('contradiction_surfaced_rate') or 0.0):.4f}  "
            f"cases={int(metrics.get('case_count') or 0)}"
        )
    if t4:
        metrics = dict(t4.get("metrics") or {})
        lines.append("- T4 longitudinal continuity:")
        lines.append(
            "  - "
            f"pass={str(bool(t4.get('pass'))).lower()}  "
            f"lift={float(metrics.get('continuity_lift') or 0.0):.4f}  "
            f"drift={int(metrics.get('self_model_drift_score') or 0)}  "
            f"drift_status={str(metrics.get('self_model_drift_status') or '')}  "
            f"goal_persistence={float(metrics.get('goal_thread_persistence_rate') or 0.0):.4f}  "
            f"applied_structural={int(metrics.get('accepted_applied_structural_candidates') or 0)}"
        )
    if t5:
        metrics = dict(t5.get("metrics") or {})
        lines.append("- T5 thread fidelity:")
        lines.append(
            "  - "
            f"pass={str(bool(t5.get('pass'))).lower()}  "
            f"precision={float(metrics.get('thread_precision') or 0.0):.4f}  "
            f"recall={float(metrics.get('thread_recall') or 0.0):.4f}  "
            f"f1={float(metrics.get('thread_f1') or 0.0):.4f}  "
            f"answerability={float(metrics.get('answerability') or 0.0):.4f}  "
            f"drift={float(metrics.get('query_drift_rate') or 0.0):.4f}  "
            f"cases={int(metrics.get('case_count') or 0)}"
        )
    ablations = dict(report.get("ablation_matrix") or {})
    if ablations:
        coverage = dict(ablations.get("coverage") or {})
        lines.append("- Ablation matrix:")
        lines.append(
            "  - "
            f"observed={int(coverage.get('observed_rows') or 0)}  "
            f"no_expected_drop={int(coverage.get('observed_no_expected_drop_rows') or 0)}  "
            f"needs_toggle={int(coverage.get('needs_runtime_toggle_rows') or 0)}  "
            f"faithful={str(bool(coverage.get('faithfulness_clean', True))).lower()}  "
            f"all_observed={str(bool(coverage.get('all_rows_observed', False))).lower()}"
        )
    real_data = dict(report.get("real_data_contrast") or {})
    if real_data:
        summary = dict(real_data.get("summary") or {})
        lines.append("- Real-data contrast:")
        lines.append(
            "  - "
            f"status={str(real_data.get('status') or '')}  "
            f"datasets={int(summary.get('dataset_count') or 0)}  "
            f"local_proxy={int(summary.get('local_proxy_count') or 0)}  "
            f"runnable={int(summary.get('runnable_count') or 0)}  "
            f"eval_smoke={int(summary.get('external_eval_smoke_count') or 0)}  "
            f"leaderboard_claims={int(summary.get('leaderboard_claim_count') or 0)}"
        )

    warnings = list(report.get("warnings") or [])
    if warnings:
        lines.append("- warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
