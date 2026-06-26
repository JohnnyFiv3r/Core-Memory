from __future__ import annotations

from pathlib import Path
from typing import Any

REAL_DATA_CONTRAST_SCHEMA = "causal_continuity.real_data_contrast.v1"


def _locomo_like_base() -> Path:
    return Path(__file__).resolve().parent.parent / "locomo_like"


def _path_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "provided": False,
            "exists": False,
            "path": "",
        }
    return {
        "provided": True,
        "exists": bool(path.exists()),
        "path": str(path),
    }


def _run_locomo_like_local_proxy(*, limit: int) -> dict[str, Any]:
    from benchmarks.locomo_like.runner import run_benchmark

    base = _locomo_like_base()
    try:
        report = run_benchmark(
            fixtures_dir=base / "fixtures",
            gold_dir=base / "gold",
            subset="local",
            limit=max(1, int(limit)),
        )
    except Exception as exc:  # pragma: no cover - defensive report path
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "status": "completed",
        "schema_version": str(report.get("schema_version") or ""),
        "metadata": {
            "runner": str((report.get("metadata") or {}).get("runner") or ""),
            "subset": str((report.get("metadata") or {}).get("subset") or ""),
            "case_count": int((report.get("metadata") or {}).get("case_count") or 0),
        },
        "totals": dict(report.get("totals") or {}),
        "latency_ms": dict(report.get("latency_ms") or {}),
        "warnings": list(report.get("warnings") or []),
    }


def _leaderboard_claim_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if bool(row.get("leaderboard_claim")))


def build_real_data_contrast(
    *,
    locomo_corpus: Path | None = None,
    run_local_proxy: bool = False,
    local_proxy_limit: int = 1,
) -> dict[str, Any]:
    """Build the PRD real-data contrast attachment.

    This object is intentionally conservative. It advertises checked-in local
    proxy coverage and external adapter readiness without presenting either as
    a public leaderboard result.
    """

    locomo_path = _path_status(locomo_corpus)
    local_execution = (
        _run_locomo_like_local_proxy(limit=local_proxy_limit)
        if run_local_proxy
        else {"status": "not_run"}
    )

    local_status = "completed" if local_execution.get("status") == "completed" else "available"
    if local_execution.get("status") == "failed":
        local_status = "failed"

    locomo_external_status = "dataset_required"
    if locomo_path["provided"] and locomo_path["exists"]:
        locomo_external_status = "dataset_available"
    elif locomo_path["provided"]:
        locomo_external_status = "corpus_path_missing"

    rows: list[dict[str, Any]] = [
        {
            "dataset_id": "locomo_like_local_proxy",
            "label": "Checked-in LOCOMO-like local proxy",
            "dataset_family": "locomo_like",
            "contrast_role": "local_proxy",
            "status": local_status,
            "availability": "checked_in_fixture_pack",
            "adapter_surface": "benchmarks.locomo_like.runner.run_benchmark",
            "benchmark_adapter_protocol": "not_required_local_proxy",
            "external_dataset_required": False,
            "network_required": False,
            "leaderboard_claim": False,
            "can_run": True,
            "run_command": (
                "python -m benchmarks.locomo_like.runner "
                f"--subset local --limit {max(1, int(local_proxy_limit))}"
            ),
            "execution": local_execution,
        },
        {
            "dataset_id": "locomo_external",
            "label": "LoCoMo external corpus",
            "dataset_family": "locomo",
            "contrast_role": "external_benchmark_adapter",
            "status": locomo_external_status,
            "availability": "requires_user_supplied_corpus",
            "adapter_surface": "benchmarks.locomo.runner",
            "benchmark_adapter_protocol": "implemented",
            "external_dataset_required": True,
            "network_required": False,
            "leaderboard_claim": False,
            "can_run": bool(locomo_path["exists"]),
            "dataset_path": locomo_path,
            "run_command": "python -m benchmarks.locomo --corpus <locomo10.json> --smoke",
            "licensing_note": "Corpus is not vendored; obtain it separately and pass --locomo-corpus.",
            "execution": {"status": "not_run"},
        },
        {
            "dataset_id": "longmemeval_external",
            "label": "LongMemEval external corpus",
            "dataset_family": "longmemeval",
            "contrast_role": "external_benchmark_adapter",
            "status": "adapter_contract_declared",
            "availability": "adapter_not_implemented",
            "adapter_surface": "",
            "benchmark_adapter_protocol": "not_implemented",
            "external_dataset_required": True,
            "network_required": False,
            "leaderboard_claim": False,
            "can_run": False,
            "run_command": "",
            "execution": {"status": "not_run"},
        },
    ]

    warnings: list[str] = []
    if locomo_external_status == "dataset_required":
        warnings.append("locomo_external_corpus_not_provided")
    if locomo_external_status == "corpus_path_missing":
        warnings.append("locomo_external_corpus_path_missing")
    warnings.append("longmemeval_adapter_not_implemented")
    if local_execution.get("status") == "failed":
        warnings.append("locomo_like_local_proxy_failed")

    top_status = "local_proxy_failed" if local_execution.get("status") == "failed" else "ready_with_local_proxy"

    return {
        "schema_version": REAL_DATA_CONTRAST_SCHEMA,
        "status": top_status,
        "adapter_contract": {
            "protocol": "benchmarks.contracts.BenchmarkAdapter",
            "required_methods": ["load_conversations", "score_answer", "score_evidence"],
            "conversation_type": "BenchmarkConversation",
            "qa_type": "BenchmarkQA",
            "contamination_guards": [
                "gold answers never materialized into the store",
                "evidence scoring stays in dataset-native id space",
                "shortcut flags disqualify unfaithful runs",
            ],
        },
        "summary": {
            "dataset_count": len(rows),
            "local_proxy_count": sum(1 for row in rows if row.get("contrast_role") == "local_proxy"),
            "external_dataset_count": sum(1 for row in rows if bool(row.get("external_dataset_required"))),
            "runnable_count": sum(1 for row in rows if bool(row.get("can_run"))),
            "leaderboard_claim_count": _leaderboard_claim_count(rows),
        },
        "datasets": rows,
        "warnings": sorted(set(warnings)),
    }
