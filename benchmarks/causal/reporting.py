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


def _rate(num: int, denom: int) -> float:
    return round((num / denom), 4) if denom else 0.0


def build_report(*, metadata: dict[str, Any], case_results: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(case_results, key=lambda r: str(r.get("case_id") or ""))
    total = len(ordered)
    passed = sum(1 for r in ordered if bool(r.get("pass")))

    edge_precisions = [float(r.get("edge_precision") or 0.0) for r in ordered]
    edge_recalls = [float(r.get("edge_recall") or 0.0) for r in ordered]
    edge_f1s = [float(r.get("edge_f1") or 0.0) for r in ordered]
    depths = [int(r.get("attribution_depth") or 0) for r in ordered]
    latencies = [float(r.get("latency_ms") or 0.0) for r in ordered]
    retrieval_latencies = [float(r.get("retrieval_ms") or 0.0) for r in ordered]

    grounding_full = sum(1 for r in ordered if bool(r.get("grounding_full")))
    root_cause_correct = sum(1 for r in ordered if bool(r.get("root_cause_correct")))

    # Distractor survival: headline metric. Only count cases that actually had
    # adversarial distractors configured — vacuous survival shouldn't inflate it.
    adversarial = [r for r in ordered if int(r.get("distractor_count") or 0) > 0]
    survived = sum(1 for r in adversarial if bool(r.get("distractor_survived")))

    warnings: list[str] = []
    for row in ordered:
        for w in (row.get("warnings") or []):
            warnings.append(str(w))

    bucket_totals: dict[str, dict[str, float]] = {}
    for row in ordered:
        for b in (row.get("bucket_labels") or []):
            d = bucket_totals.setdefault(str(b), {"total": 0.0, "pass": 0.0})
            d["total"] += 1.0
            if bool(row.get("pass")):
                d["pass"] += 1.0

    per_bucket = {
        b: {
            "total": int(v["total"]),
            "pass": int(v["pass"]),
            "accuracy": round((v["pass"] / v["total"]) if v["total"] else 0.0, 4),
        }
        for b, v in sorted(bucket_totals.items(), key=lambda kv: kv[0])
    }

    return {
        "schema_version": "causal_benchmark_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "totals": {
            "cases": total,
            "pass": passed,
            "fail": total - passed,
            "accuracy": _rate(passed, total),
        },
        "causal_metrics": {
            "edge_precision_mean": round(_mean(edge_precisions), 4),
            "edge_recall_mean": round(_mean(edge_recalls), 4),
            "edge_f1_mean": round(_mean(edge_f1s), 4),
            "grounding_full_rate": _rate(grounding_full, total),
            "root_cause_accuracy": _rate(root_cause_correct, total),
            "attribution_depth_mean": round(_mean([float(d) for d in depths]), 3),
            "attribution_depth_max": max(depths) if depths else 0,
        },
        "distractor_survival": {
            "adversarial_case_count": int(len(adversarial)),
            "survived_count": int(survived),
            # Headline one-number metric: causal traversal beats pure similarity.
            "survival_rate": _rate(survived, len(adversarial)),
        },
        "latency_ms": {
            "mean": round(_mean(latencies), 3),
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "retrieval_mean": round(_mean(retrieval_latencies), 3),
        },
        "per_bucket": per_bucket,
        "warnings": sorted(set(warnings)),
        "cases": ordered,
    }


def render_summary(report: dict[str, Any]) -> str:
    totals = dict(report.get("totals") or {})
    cm = dict(report.get("causal_metrics") or {})
    ds = dict(report.get("distractor_survival") or {})
    lat = dict(report.get("latency_ms") or {})

    lines = [
        "Causal-Chain Reconstruction Benchmark",
        f"- cases: {totals.get('cases', 0)}  pass: {totals.get('pass', 0)}  fail: {totals.get('fail', 0)}  accuracy: {totals.get('accuracy', 0.0):.4f}",
        "- causal metrics:",
        f"  - edge precision / recall / f1: {cm.get('edge_precision_mean', 0.0):.4f} / {cm.get('edge_recall_mean', 0.0):.4f} / {cm.get('edge_f1_mean', 0.0):.4f}",
        f"  - grounding full rate: {cm.get('grounding_full_rate', 0.0):.4f}",
        f"  - root cause accuracy: {cm.get('root_cause_accuracy', 0.0):.4f}",
        f"  - attribution depth mean/max: {cm.get('attribution_depth_mean', 0.0):.3f} / {cm.get('attribution_depth_max', 0)}",
        "- DISTRACTOR SURVIVAL (headline):",
        f"  - adversarial cases: {ds.get('adversarial_case_count', 0)}  survived: {ds.get('survived_count', 0)}",
        f"  - survival rate: {ds.get('survival_rate', 0.0):.4f}",
        f"- latency mean/p95 ms: {lat.get('mean', 0.0):.3f} / {lat.get('p95', 0.0):.3f}",
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
