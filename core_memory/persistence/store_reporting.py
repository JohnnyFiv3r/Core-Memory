from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from core_memory.persistence import events


def _window_start_from_since(since: str) -> datetime | None:
    window_start = None
    m = re.fullmatch(r"(\d+)([dh])", (since or "").strip().lower())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
        window_start = datetime.now(timezone.utc) - delta
    return window_start


def metrics_report_for_store(store: Any, since: str = "7d") -> dict[str, Any]:
    """Deterministic metrics aggregation from metrics stream for MemoryStore."""
    window_start = _window_start_from_since(since)

    rows = []
    for row in events.iter_metrics(store.root) or []:
        ts = row.get("ts")
        if window_start and ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt < window_start:
                    continue
            except ValueError:
                continue
        rows.append(row)

    rows = sorted(rows, key=lambda r: (r.get("ts", ""), r.get("run_id", "")))
    if not rows:
        return {
            "runs": 0,
            "repeat_failure_rate": 0.0,
            "decision_flip_rate": 0.0,
            "median_steps": 0,
            "median_tool_calls": 0,
            "compression_ratio": 0.0,
            "rationale_recall_avg": 0.0,
        }

    def median(values: list[int]) -> float:
        s = sorted(values)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    runs = len(rows)
    repeat_fail = sum(1 for r in rows if r.get("repeat_failure")) / runs
    flips = sum(int(r.get("unjustified_flips", 0) or 0) for r in rows)
    decision_conflicts = sum(int(r.get("decision_conflicts", 0) or 0) for r in rows)
    flip_rate = (flips / decision_conflicts) if decision_conflicts else 0.0

    steps = [int(r.get("steps", 0) or 0) for r in rows]
    tools = [int(r.get("tool_calls", 0) or 0) for r in rows]
    cr = [float(r.get("compression_ratio", 0) or 0) for r in rows if float(r.get("compression_ratio", 0) or 0) > 0]
    rr = [int(r.get("rationale_recall_score", 0) or 0) for r in rows]

    return {
        "runs": runs,
        "repeat_failure_rate": round(repeat_fail, 4),
        "decision_flip_rate": round(flip_rate, 4),
        "median_steps": median(steps),
        "median_tool_calls": median(tools),
        "compression_ratio": round(sum(cr) / len(cr), 4) if cr else 0.0,
        "rationale_recall_avg": round(sum(rr) / len(rr), 4) if rr else 0.0,
    }


def autonomy_report_for_store(store: Any, since: str = "7d") -> dict[str, Any]:
    """Aggregate autonomy KPIs from metrics stream for MemoryStore."""
    window_start = _window_start_from_since(since)

    rows = []
    for row in events.iter_metrics(store.root) or []:
        if row.get("task_id") != "autonomy_kpi":
            continue
        ts = row.get("ts")
        if window_start and ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt < window_start:
                    continue
            except ValueError:
                continue
        rows.append(row)

    rows = sorted(rows, key=lambda r: (r.get("ts", ""), r.get("run_id", "")))
    total = len(rows)
    if total == 0:
        return {
            "runs": 0,
            "repeat_failure_rate": 0.0,
            "unjustified_flip_rate": 0.0,
            "constraint_violation_rate": 0.0,
            "wrong_transfer_rate": 0.0,
            "goal_carryover_rate": 0.0,
            "contradiction_resolution_rate": 0.0,
            "contradiction_latency_avg": 0.0,
        }

    def rate(pred):
        return round(sum(1 for r in rows if pred(r)) / total, 4)

    lat = [int(r.get("kpi_contradiction_latency_turns", 0) or 0) for r in rows if r.get("kpi_contradiction_resolved")]
    lat_avg = round(sum(lat) / len(lat), 4) if lat else 0.0

    return {
        "runs": total,
        "repeat_failure_rate": rate(lambda r: bool(r.get("repeat_failure"))),
        "unjustified_flip_rate": rate(lambda r: bool(r.get("unjustified_flips"))),
        "constraint_violation_rate": rate(lambda r: bool(r.get("kpi_constraint_violation"))),
        "wrong_transfer_rate": rate(lambda r: bool(r.get("kpi_wrong_transfer"))),
        "goal_carryover_rate": rate(lambda r: bool(r.get("kpi_goal_carryover"))),
        "contradiction_resolution_rate": rate(lambda r: bool(r.get("kpi_contradiction_resolved"))),
        "contradiction_latency_avg": lat_avg,
    }


def schema_quality_report_for_store(store: Any, write_path: str | None = None) -> dict[str, Any]:
    """Report required-field warnings and promotion gate blockers for MemoryStore."""
    index = store._read_json(store.beads_dir / "index.json")
    beads = list((index.get("beads") or {}).values())

    total_by_type: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    warnings_by_type: dict[str, int] = {}
    warning_keys: dict[str, int] = {}
    promotion_block_reasons: dict[str, int] = {}

    def inc(d: dict[str, int], k: str, n: int = 1):
        d[k] = d.get(k, 0) + n

    for bead in beads:
        t = str(bead.get("type") or "")
        st = str(bead.get("status") or "")
        inc(total_by_type, t)
        inc(status_counts, st)

        for w in (bead.get("validation_warnings") or []):
            inc(warnings_by_type, t)
            inc(warning_keys, str(w))

        if st != "open" or t not in {"decision", "lesson", "outcome", "precedent"}:
            continue

        because = bool(bead.get("because"))
        detail = bool((bead.get("detail") or "").strip())
        has_evidence = bool(store._has_evidence(bead))
        has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))

        if t == "decision" and not (because and (has_evidence or detail)):
            inc(promotion_block_reasons, "decision_missing_because_and_evidence_or_detail")
        elif t == "lesson" and not because:
            inc(promotion_block_reasons, "lesson_missing_because")
        elif t == "outcome":
            result = str(bead.get("result") or "").strip().lower()
            if result not in {"resolved", "failed", "partial", "confirmed"}:
                inc(promotion_block_reasons, "outcome_invalid_result")
            if not (has_link or has_evidence):
                inc(promotion_block_reasons, "outcome_missing_link_or_evidence")
        elif t == "precedent":
            if not (str(bead.get("condition") or "").strip() and str(bead.get("action") or "").strip()):
                inc(promotion_block_reasons, "precedent_missing_condition_action")

    report = {
        "ok": True,
        "total_beads": len(beads),
        "status_counts": status_counts,
        "total_by_type": total_by_type,
        "warnings_by_type": warnings_by_type,
        "top_warning_keys": sorted(warning_keys.items(), key=lambda kv: kv[1], reverse=True)[:20],
        "promotion_block_reasons": promotion_block_reasons,
    }

    if write_path:
        out = Path(write_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Schema Quality Report ({datetime.now(timezone.utc).isoformat()})",
            "",
            f"- Total beads: {report['total_beads']}",
            f"- Status counts: {report['status_counts']}",
            f"- Type counts: {report['total_by_type']}",
            "",
            "## Validation warnings",
            str(report["top_warning_keys"] or "none"),
            "",
            "## Promotion block reasons",
            str(report["promotion_block_reasons"] or "none"),
        ]
        out.write_text("\n".join(lines), encoding="utf-8")
        report["written"] = str(out)

    return report
