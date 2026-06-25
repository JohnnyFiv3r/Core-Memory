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
    }


def _faithfulness_by_strategy(t1_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, report in sorted((t1_report.get("strategy_reports") or {}).items()):
        meta = dict(report.get("metadata") or {})
        flags = dict(meta.get("faithfulness") or meta.get("shortcut_flags") or {})
        if flags:
            out[str(name)] = flags
    return out


def build_suite_report(*, metadata: dict[str, Any], t1_report: dict[str, Any]) -> dict[str, Any]:
    by_strategy = _faithfulness_by_strategy(t1_report)
    is_faithful = all(bool(v.get("is_faithful")) for v in by_strategy.values()) if by_strategy else True

    warnings: list[str] = []
    for report in (t1_report.get("strategy_reports") or {}).values():
        warnings.extend(str(w) for w in (report.get("warnings") or []))

    return {
        "schema_version": "causal_continuity_report.v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "faithfulness": {
            "is_faithful": bool(is_faithful),
            "by_strategy": by_strategy,
        },
        "headlines": {
            "t1_causal_chain_reconstruction": _extract_t1_headlines(t1_report),
        },
        "tasks": {
            "t1_causal_chain_reconstruction": t1_report,
        },
        "warnings": sorted(set(warnings)),
    }


def render_summary(report: dict[str, Any]) -> str:
    meta = dict(report.get("metadata") or {})
    faith = dict(report.get("faithfulness") or {})
    t1 = dict((report.get("tasks") or {}).get("t1_causal_chain_reconstruction") or {})
    matrix = dict(t1.get("strategy_matrix") or {})

    lines = [
        "Causal-Continuity Evaluation Suite",
        f"- suite: {meta.get('suite', 'causal_continuity')}  task_count: {meta.get('task_count', 1)}",
        f"- faithful: {str(bool(faith.get('is_faithful', True))).lower()}",
        "- T1 causal-chain reconstruction:",
    ]

    for name, row in sorted(matrix.items()):
        lines.append(
            "  - "
            f"{name}: CSR={float(row.get('causal_survival_rate') or 0.0):.4f}  "
            f"root={float(row.get('root_cause_accuracy') or 0.0):.4f}  "
            f"edge_f1={float(row.get('edge_f1_mean') or 0.0):.4f}  "
            f"cases={int(row.get('cases') or 0)}"
        )

    warnings = list(report.get("warnings") or [])
    if warnings:
        lines.append("- warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
