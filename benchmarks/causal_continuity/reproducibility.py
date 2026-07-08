from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner import run_suite
from .t1 import available_strategies

REPRODUCIBILITY_REPORT_SCHEMA = "causal_continuity.reproducibility.v1"


def _repo_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def _t5_ordered_outputs(report: dict[str, Any]) -> list[dict[str, Any]]:
    t5 = dict((report.get("tasks") or {}).get("t5_thread_fidelity") or {})
    outputs: list[dict[str, Any]] = []
    for case in list(t5.get("cases") or []):
        if not isinstance(case, dict):
            continue
        key_by_id = {
            str(k): str(v)
            for k, v in dict(case.get("bead_key_by_id") or {}).items()
            if str(k).strip() and str(v).strip()
        }
        gold_ids = [str(x) for x in list(case.get("gold_thread_bead_ids") or [])]
        drift_ids = [str(x) for x in list(case.get("drift_thread_bead_ids") or [])]
        stable_by_id = {bid: f"gold:{i}" for i, bid in enumerate(gold_ids)}
        stable_by_id.update({
            bid: f"drift:{i}"
            for i, bid in enumerate(drift_ids)
            if bid not in stable_by_id
        })

        def stable_ids(values: list[Any]) -> list[str]:
            out: list[str] = []
            for value in values:
                bead_id = str(value or "")
                out.append(key_by_id.get(bead_id) or stable_by_id.get(bead_id, "other"))
            return out

        def selected_trace_ids(step: dict[str, Any]) -> list[Any]:
            trace_values = list(step.get("trace_ids") or [])
            selected = [
                str(x)
                for x in list((dict(step.get("selected_storyline") or {})).get("bead_ids") or [])
                if str(x).strip()
            ]
            if not selected:
                return trace_values
            selected_set = set(selected)
            filtered = [x for x in trace_values if str(x) in selected_set]
            present = {str(x) for x in filtered}
            return filtered + [x for x in selected if x not in present]

        loop = dict(case.get("loop") or {})
        one_shot = dict(case.get("one_shot_anchor_baseline") or {})
        outputs.append({
            "case_id": str(case.get("case_id") or ""),
            "returned_thread_order": list(case.get("returned_thread_keys") or stable_ids(list(case.get("returned_thread_bead_ids") or []))),
            "one_shot_returned_order": list(one_shot.get("returned_keys") or stable_ids(list(one_shot.get("returned_bead_ids") or []))),
            "trace_steps": [
                {
                    "step": int(step.get("step") or 0),
                    "trace_anchor_order": stable_ids(list(step.get("trace_anchor_ids") or [])),
                    "trace_order": stable_ids(selected_trace_ids(step)),
                    "selected_storyline_key": "|".join(
                        str(x)
                        for x in list((dict(step.get("selected_storyline") or {})).get("bead_keys") or [])
                    ),
                }
                for step in list(loop.get("steps") or [])
                if isinstance(step, dict)
            ],
        })
    return outputs


def _headline_signature(report: dict[str, Any]) -> dict[str, Any]:
    t1 = dict((report.get("tasks") or {}).get("t1_causal_chain_reconstruction") or {})
    matrix = dict(t1.get("strategy_matrix") or {})
    ablations = dict(report.get("ablation_matrix") or {})
    real_data = dict(report.get("real_data_contrast") or {})
    return {
        "faithful": bool((report.get("faithfulness") or {}).get("is_faithful", True)),
        "t1_status_by_strategy": {
            name: str(row.get("status") or "")
            for name, row in sorted(matrix.items())
            if isinstance(row, dict)
        },
        "t1_csr_by_strategy": {
            name: row.get("causal_survival_rate")
            for name, row in sorted(matrix.items())
            if isinstance(row, dict)
        },
        "t2": dict((report.get("headlines") or {}).get("t2_calibration_reliability") or {}),
        "t3": dict((report.get("headlines") or {}).get("t3_temporal_state_selection") or {}),
        "t4": dict((report.get("headlines") or {}).get("t4_longitudinal_continuity") or {}),
        "t5": dict((report.get("headlines") or {}).get("t5_thread_fidelity") or {}),
        "ablation_coverage": dict(ablations.get("coverage") or {}),
        "real_data_summary": dict(real_data.get("summary") or {}),
    }


def _run_local_report() -> dict[str, Any]:
    return run_suite(
        fixtures_dir=Path("benchmarks/causal/fixtures"),
        gold_dir=Path("benchmarks/causal/gold"),
        strategies=list(available_strategies()),
        tasks=["t1", "t2", "t3", "t4", "t5"],
        subset="local",
        limit=1,
        run_ablation_toggles=True,
        include_real_data_contrast=True,
    )


def run_reproducibility_check(*, repeats: int = 5) -> dict[str, Any]:
    repeats = max(2, int(repeats))
    runs: list[dict[str, Any]] = []
    warnings: set[str] = set()
    for i in range(repeats):
        report = _run_local_report()
        ordered_outputs = _t5_ordered_outputs(report)
        headline = _headline_signature(report)
        signature = {
            "headline": headline,
            "ordered_topk": ordered_outputs,
        }
        runs.append({
            "run_index": i + 1,
            "signature_digest": _digest(signature),
            "headline_digest": _digest(headline),
            "ordered_topk_digest": _digest(ordered_outputs),
            "ordered_topk": ordered_outputs,
            "warnings": list(report.get("warnings") or []),
        })
        warnings.update(str(w) for w in list(report.get("warnings") or []))

    signature_digests = [str(row.get("signature_digest") or "") for row in runs]
    headline_digests = [str(row.get("headline_digest") or "") for row in runs]
    topk_digests = [str(row.get("ordered_topk_digest") or "") for row in runs]
    headline_stable = len(set(headline_digests)) == 1
    topk_stable = len(set(topk_digests)) == 1
    deterministic = headline_stable and topk_stable
    return {
        "schema_version": REPRODUCIBILITY_REPORT_SCHEMA,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _repo_commit(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "config": {
            "repeats": repeats,
            "suite_command": (
                "python -m benchmarks.causal_continuity.runner --subset local "
                "--limit 1 --strategies all --run-ablation-toggles "
                "--include-real-data-contrast"
            ),
        },
        "determinism": {
            "passed": deterministic,
            "signature_digest": signature_digests[0] if signature_digests else "",
            "headline_digest": headline_digests[0] if headline_digests else "",
            "ordered_topk_digest": topk_digests[0] if topk_digests else "",
            "stable_headlines": headline_stable,
            "stable_ordered_topk": len(set(topk_digests)) == 1,
            "run_count": len(runs),
            "status": "stable" if deterministic else ("unstable_ordered_topk" if headline_stable else "unstable_headlines"),
        },
        "runs": runs,
        "warnings": sorted(warnings),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Run causal-continuity reproducibility checks")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--out", default="")
    p.add_argument("--pretty", action="store_true")
    p.add_argument("--require-pass", action="store_true")
    args = p.parse_args()

    report = run_reproducibility_check(repeats=int(args.repeats))
    text = json.dumps(report, indent=2 if args.pretty else None)
    print(text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.require_pass and not bool((report.get("determinism") or {}).get("passed")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
