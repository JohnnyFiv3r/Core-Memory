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
    return "\n".join(lines)
