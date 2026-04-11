from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any


def _mean(nums: list[float]) -> float:
    return float(statistics.mean(nums)) if nums else 0.0


def _percentile(nums: list[float], p: float) -> float:
    if not nums:
        return 0.0
    data = sorted(nums)
    if len(data) == 1:
        return float(data[0])
    rank = (len(data) - 1) * max(0.0, min(1.0, p))
    lo = int(rank)
    hi = min(lo + 1, len(data) - 1)
    frac = rank - lo
    return float(data[lo] + (data[hi] - data[lo]) * frac)


def build_report(*, metadata: dict[str, Any], case_results: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(case_results, key=lambda r: str(r.get("case_id") or ""))
    total = len(ordered)
    passed = sum(1 for r in ordered if bool(r.get("pass")))
    failed = total - passed

    bucket_totals: dict[str, dict[str, float]] = {}
    warnings: list[str] = []
    latencies = [float(r.get("latency_ms") or 0.0) for r in ordered]
    setup_latencies = [float(r.get("write_setup_ms") or 0.0) for r in ordered]
    retrieval_latencies = [float(r.get("retrieval_ms") or 0.0) for r in ordered]
    pending_before = [int(((r.get("queue_before_query") or {}).get("pending_total") or 0)) for r in ordered]
    pending_after = [int(((r.get("queue_after_query") or {}).get("pending_total") or 0)) for r in ordered]
    backend_mode_counts: dict[str, int] = {}

    dreamer_accepted_total = 0
    dreamer_accepted_applied_total = 0
    dreamer_accepted_used_total = 0
    dreamer_accepted_applied_used_total = 0
    used_case_total = 0
    used_case_pass = 0
    non_used_case_total = 0
    non_used_case_pass = 0

    for row in ordered:
        mode = str(row.get("benchmark_backend_mode") or "")
        if mode:
            backend_mode_counts[mode] = int(backend_mode_counts.get(mode, 0)) + 1
        diag = dict(row.get("semantic_backend") or {})
        if bool(diag.get("ok")):
            if str(diag.get("concurrency_warning") or "").strip():
                warnings.append(str(diag.get("concurrency_warning")))

        dc = dict(row.get("dreamer_correlation") or {})
        accepted = int(dc.get("accepted_total") or 0)
        used = int(dc.get("accepted_used_total") or 0)
        applied = int(dc.get("accepted_applied_total") or 0)
        applied_used = int(dc.get("accepted_applied_used_total") or 0)
        dreamer_accepted_total += accepted
        dreamer_accepted_applied_total += applied
        dreamer_accepted_used_total += used
        dreamer_accepted_applied_used_total += applied_used

        if accepted > 0 and used > 0:
            used_case_total += 1
            if bool(row.get("pass")):
                used_case_pass += 1
        else:
            non_used_case_total += 1
            if bool(row.get("pass")):
                non_used_case_pass += 1

    for row in ordered:
        for w in (row.get("warnings") or []):
            warnings.append(str(w))
        for b in (row.get("bucket_labels") or []):
            d = bucket_totals.setdefault(str(b), {"total": 0.0, "pass": 0.0, "fail": 0.0})
            d["total"] += 1.0
            if bool(row.get("pass")):
                d["pass"] += 1.0
            else:
                d["fail"] += 1.0

    per_bucket = {
        b: {
            "total": int(v["total"]),
            "pass": int(v["pass"]),
            "fail": int(v["fail"]),
            "accuracy": round((v["pass"] / v["total"]) if v["total"] else 0.0, 4),
        }
        for b, v in sorted(bucket_totals.items(), key=lambda kv: kv[0])
    }

    report = {
        "schema_version": "locomo_like_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "totals": {
            "cases": total,
            "pass": passed,
            "fail": failed,
            "accuracy": round((passed / total) if total else 0.0, 4),
        },
        "latency_ms": {
            "mean": round(_mean(latencies), 3),
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
        },
        "latency_breakdown_ms": {
            "write_setup_mean": round(_mean(setup_latencies), 3),
            "write_setup_p95": round(_percentile(setup_latencies, 0.95), 3),
            "retrieval_mean": round(_mean(retrieval_latencies), 3),
            "retrieval_p95": round(_percentile(retrieval_latencies, 0.95), 3),
        },
        "queue_observability": {
            "pending_before_query_max": max(pending_before) if pending_before else 0,
            "pending_after_query_max": max(pending_after) if pending_after else 0,
            "pending_before_query_mean": round(_mean([float(x) for x in pending_before]), 3) if pending_before else 0.0,
            "pending_after_query_mean": round(_mean([float(x) for x in pending_after]), 3) if pending_after else 0.0,
        },
        "backend_observability": {
            "backend_mode_counts": dict(sorted(backend_mode_counts.items(), key=lambda kv: kv[0])),
            "semantic_mode": str((metadata or {}).get("semantic_mode") or ""),
            "backend_mode": str((metadata or {}).get("backend_mode") or ""),
        },
        "dreamer_correlation": {
            "accepted_candidates_total": int(dreamer_accepted_total),
            "accepted_applied_total": int(dreamer_accepted_applied_total),
            "accepted_used_in_retrieval_total": int(dreamer_accepted_used_total),
            "accepted_applied_used_in_retrieval_total": int(dreamer_accepted_applied_used_total),
            "retrieval_use_rate": round((dreamer_accepted_used_total / dreamer_accepted_total), 4)
            if dreamer_accepted_total > 0
            else None,
            "accuracy_when_used": round((used_case_pass / used_case_total), 4) if used_case_total > 0 else None,
            "accuracy_when_not_used": round((non_used_case_pass / non_used_case_total), 4) if non_used_case_total > 0 else None,
            "used_case_count": int(used_case_total),
            "non_used_case_count": int(non_used_case_total),
        },
        "token_usage": None,
        "per_bucket": per_bucket,
        "warnings": sorted(set(warnings)),
        "cases": ordered,
    }
    return report


def render_summary(report: dict[str, Any]) -> str:
    totals = dict(report.get("totals") or {})
    lines = [
        "LOCOMO-like Benchmark Summary",
        f"- cases: {totals.get('cases', 0)}",
        f"- pass: {totals.get('pass', 0)}",
        f"- fail: {totals.get('fail', 0)}",
        f"- accuracy: {totals.get('accuracy', 0.0):.4f}",
        f"- latency mean/p95 ms: {(report.get('latency_ms') or {}).get('mean', 0.0):.3f} / {(report.get('latency_ms') or {}).get('p95', 0.0):.3f}",
    ]
    per_bucket = dict(report.get("per_bucket") or {})
    if per_bucket:
        lines.append("- bucket accuracy:")
        for b, v in per_bucket.items():
            lines.append(f"  - {b}: {float(v.get('accuracy') or 0.0):.4f} ({int(v.get('pass') or 0)}/{int(v.get('total') or 0)})")
    warns = list(report.get("warnings") or [])
    if warns:
        lines.append("- warnings:")
        for w in warns:
            lines.append(f"  - {w}")

    dc = dict(report.get("dreamer_correlation") or {})
    if dc:
        lines.append("- dreamer correlation:")
        lines.append(f"  - accepted candidates: {int(dc.get('accepted_candidates_total') or 0)}")
        lines.append(f"  - accepted used in retrieval: {int(dc.get('accepted_used_in_retrieval_total') or 0)}")
        lines.append(f"  - retrieval use rate: {dc.get('retrieval_use_rate')}")

    mc = dict(report.get("myelination_comparison") or {})
    if mc:
        b = dict(mc.get("baseline") or {})
        e = dict(mc.get("enabled") or {})
        lines.append("- myelination comparison:")
        lines.append(f"  - baseline accuracy: {b.get('accuracy')}")
        lines.append(f"  - enabled accuracy: {e.get('accuracy')}")
        lines.append(f"  - accuracy delta: {mc.get('accuracy_delta')}")
    return "\n".join(lines)
