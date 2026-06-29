from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .attestations import normalize_evidence_attestation, scope_attested


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


def build_evidence_manifest(report: dict[str, Any]) -> dict[str, Any]:
    """Summarize which evidence claims the report can honestly support."""

    tasks = dict(report.get("tasks") or {})
    t1 = dict(tasks.get("t1_causal_chain_reconstruction") or {})
    matrix = dict(t1.get("strategy_matrix") or {})
    t2 = dict(tasks.get("t2_calibration_reliability") or {})
    t3 = dict(tasks.get("t3_temporal_state_selection") or {})
    t4 = dict(tasks.get("t4_longitudinal_continuity") or {})
    t5 = dict(tasks.get("t5_thread_fidelity") or {})
    ablations = dict(report.get("ablation_matrix") or {})
    real_data = dict(report.get("real_data_contrast") or {})
    faith = dict(report.get("faithfulness") or {})
    attestation = normalize_evidence_attestation(report.get("evidence_attestation"))

    local_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("execution_mode") or "") in {"core_memory", "local_baseline"}
        and int(row.get("cases") or 0) > 0
    ]
    proxy_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("execution_mode") or "") == "local_proxy"
    ]
    adapter_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("execution_mode") or "") in {"adapter_command", "adapter_fake"}
    ]
    unavailable_adapter_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("adapter_status") or "") == "unavailable"
    ]

    required_tasks_present = all(bool(x) for x in (t1, t2, t3, t4, t5))
    t1_core = dict(matrix.get("core_memory_full") or {})
    t1_core_ready = bool(t1_core) and int(t1_core.get("cases") or 0) > 0 and int(t1_core.get("pass") or 0) > 0
    deterministic_tasks_ready = all(bool(x.get("pass")) for x in (t2, t3, t4, t5) if x)
    coverage = dict(ablations.get("coverage") or {})
    ablations_ready = (
        bool(coverage)
        and int(coverage.get("needs_runtime_toggle_rows") or 0) == 0
        and int(coverage.get("observed_no_expected_drop_rows") or 0) == 0
        and bool(coverage.get("faithfulness_clean", True))
    )
    local_ready = (
        bool(faith.get("is_faithful", True))
        and required_tasks_present
        and t1_core_ready
        and deterministic_tasks_ready
        and ablations_ready
    )

    provider_command_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("execution_mode") or "") == "adapter_command"
        and int(row.get("cases") or 0) > 0
        and str(row.get("adapter_status") or "") == "completed"
    ]
    provider_leaderboard_rows = [
        name
        for name, row in sorted(matrix.items())
        if isinstance(row, dict)
        and str(row.get("execution_mode") or "") == "adapter_command"
        and bool(row.get("leaderboard_claim"))
    ]
    provider_attested = bool(provider_command_rows) and scope_attested(
        attestation,
        "provider_backed_comparison",
        names=provider_command_rows,
    )
    provider_ready = bool(provider_leaderboard_rows) or provider_attested
    provider_status = "not_configured"
    if provider_ready:
        provider_status = "attested_provider_comparison"
    elif provider_command_rows:
        provider_status = "configured_adapter_executed"
    elif adapter_rows:
        provider_status = "adapter_contract_exercised"
    elif unavailable_adapter_rows:
        provider_status = "unavailable"

    real_summary = dict(real_data.get("summary") or {})
    real_status = str(real_data.get("status") or ("not_requested" if not real_data else "unknown"))
    external_eval_count = int(real_summary.get("external_eval_smoke_count") or 0)
    leaderboard_count = int(real_summary.get("leaderboard_claim_count") or 0)
    real_dataset_ids = [
        str(row.get("dataset_id") or "")
        for row in list(real_data.get("datasets") or [])
        if isinstance(row, dict)
        and bool(row.get("external_dataset_required"))
        and dict(row.get("evaluation_smoke_execution") or {}).get("status") == "completed"
        and str(row.get("dataset_id") or "").strip()
    ]
    real_attested = external_eval_count > 0 and scope_attested(
        attestation,
        "real_data_leaderboard",
        names=real_dataset_ids,
    )
    real_ready = leaderboard_count > 0 or real_attested
    if real_ready:
        real_tier_status = "leaderboard_claim_present"
    elif external_eval_count > 0:
        real_tier_status = "evaluation_smoke_only"
    elif real_data:
        real_tier_status = "dataset_required"
    else:
        real_tier_status = "not_requested"

    judge = dict((dict(t5.get("metadata") or {}).get("judge") or {}))
    judge_kind = str(judge.get("kind") or "deterministic")
    judge_status = str(judge.get("status") or "not_run")
    is_llm_judge = bool(judge.get("is_llm_judge"))
    t5_attested = bool(is_llm_judge) and judge_kind == "llm" and judge_status == "completed" and scope_attested(
        attestation,
        "t5_llm_judge_primary",
    )
    if t5_attested:
        judge_tier_status = "attested_llm_primary"
    elif is_llm_judge and judge_status == "completed":
        judge_tier_status = "supplemental_llm_executed"
    elif judge_kind == "deterministic" and judge_status == "completed":
        judge_tier_status = "deterministic_default"
    elif judge_status == "unavailable":
        judge_tier_status = "supplemental_unavailable"
    else:
        judge_tier_status = judge_status or "not_run"

    local_blockers: list[str] = []
    if not required_tasks_present:
        local_blockers.append("missing_required_tasks")
    if not bool(faith.get("is_faithful", True)):
        local_blockers.append("faithfulness_failed")
    if not t1_core_ready:
        local_blockers.append("t1_core_memory_full_not_passing")
    if not deterministic_tasks_ready:
        local_blockers.append("deterministic_tasks_not_passing")
    if not ablations_ready:
        local_blockers.append("runtime_ablation_evidence_missing_or_incomplete")

    provider_blockers: list[str] = []
    if not provider_command_rows:
        provider_blockers.append("no_provider_command_adapter_run")
    if not provider_ready:
        provider_blockers.append("no_provider_leaderboard_claim_rows")
    if provider_command_rows and not provider_ready:
        provider_blockers.append("missing_provider_comparison_attestation")

    real_data_blockers: list[str] = []
    if not real_ready:
        real_data_blockers.append("no_real_data_leaderboard_claim_rows")
    if external_eval_count == 0:
        real_data_blockers.append("no_external_corpus_eval_smoke")
    if external_eval_count > 0 and not real_ready:
        real_data_blockers.append("missing_real_data_leaderboard_attestation")

    return {
        "schema_version": "causal_continuity.evidence_manifest.v1",
        "tiers": {
            "local_deterministic": {
                "status": "ready" if local_ready else "incomplete",
                "claim_scope": "checked_in_fixture_and_runtime_ablation_evidence",
                "leaderboard_claim": False,
                "rows": local_rows,
                "blockers": local_blockers,
            },
            "proxy_comparator": {
                "status": "proxy_only" if proxy_rows else "not_present",
                "claim_scope": "local_proxy_comparison_only",
                "leaderboard_claim": False,
                "rows": proxy_rows,
            },
            "configured_adapter": {
                "status": provider_status,
                "claim_scope": "configured_t1_command_or_contract_adapter",
                "leaderboard_claim": provider_ready,
                "rows": adapter_rows,
                "command_adapter_rows": provider_command_rows,
                "unavailable_rows": unavailable_adapter_rows,
                "attested": provider_attested,
                "blockers": provider_blockers,
            },
            "real_data_external": {
                "status": real_tier_status,
                "claim_scope": "external_corpus_contrast",
                "leaderboard_claim": real_ready,
                "external_eval_smoke_count": external_eval_count,
                "leaderboard_claim_count": leaderboard_count,
                "attested": real_attested,
                "attested_dataset_ids": real_dataset_ids if real_attested else [],
                "blockers": real_data_blockers,
            },
            "t5_judge": {
                "status": judge_tier_status,
                "claim_scope": "answerability_judge",
                "judge_kind": judge_kind,
                "judge_status": judge_status,
                "is_llm_judge": is_llm_judge,
                "attested": t5_attested,
                "primary_claim": t5_attested,
            },
        },
        "claim_gates": {
            "local_fixture_claim_ready": local_ready,
            "provider_backed_comparison_ready": provider_ready,
            "real_data_leaderboard_ready": real_ready,
            "t5_llm_judge_primary_claim_ready": t5_attested,
        },
        "evidence_attestation": {
            "status": str(attestation.get("status") or "not_provided"),
            "accepted_scopes": sorted((attestation.get("accepted_scopes") or {}).keys()),
            "rejected_count": len(list(attestation.get("rejected") or [])),
        },
        "notes": [
            "proxy_rows_are_not_leaderboard_claims",
            "configured_adapters_require_explicit_external_system_documentation_for_public_comparison_claims",
            "t5_llm_judge_is_supplemental_unless_a_future_report_explicitly_promotes_it",
        ],
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
