from __future__ import annotations

"""Append-only semantic task run receipts."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock

from .contracts import (
    DEFAULT_TASK_MODEL_TIERS,
    SEMANTIC_TASK_RUNS_CONTRACT,
    SEMANTIC_TASK_RUNS_SCHEMA,
    SEMANTIC_TASK_SUMMARY_CONTRACT,
    SemanticTaskRequest,
    SemanticTaskResult,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def semantic_task_runs_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "semantic-task-runs.jsonl"


def receipt_from_result(request: SemanticTaskRequest, result: SemanticTaskResult) -> dict[str, Any]:
    profile = result.model_profile.as_dict() if result.model_profile else {}
    model_tier = str(profile.get("tier") or request.model_tier or DEFAULT_TASK_MODEL_TIERS.get(request.task_type) or "")
    receipt_id = result.receipt_id or f"semrun-{stable_hash([result.task_id, result.status, now_iso()])}"
    return {
        "schema": SEMANTIC_TASK_RUNS_SCHEMA,
        "contract": SEMANTIC_TASK_RUNS_CONTRACT,
        "receipt_id": receipt_id,
        "recorded_at": now_iso(),
        "task_id": result.task_id,
        "task_type": result.task_type,
        "status": result.status,
        "ok": bool(result.ok),
        "model_tier": model_tier,
        "model_profile": profile,
        "prompt_version": result.prompt_version or request.prompt_version,
        "rubric_version": result.rubric_version or request.rubric_version,
        "output_schema": result.output_schema or request.output_schema,
        "input_hash": result.input_hash or stable_hash(
            {
                "task_type": request.task_type,
                "prompt": request.prompt,
                "payload": request.payload,
                "idempotency_key": request.idempotency_key,
            }
        ),
        "output_hash": result.output_hash or stable_hash(
            {
                "output_text": result.output_text,
                "output_json": result.output_json,
                "error": result.error,
            }
        ),
        "latency_ms": result.latency_ms,
        "token_usage": dict(result.token_usage or {}),
        "fallback_mode": result.fallback_mode or request.fallback_mode,
        "authority_boundary": result.authority_boundary or request.authority_boundary,
        "evidence_refs": list(result.evidence_refs or request.evidence_refs or []),
        "result_refs": dict(result.result_refs or {}),
        "error": result.error,
        "metadata": dict(result.metadata or request.metadata or {}),
    }


def record_semantic_task_run(
    root: str | Path,
    request: SemanticTaskRequest,
    result: SemanticTaskResult,
) -> dict[str, Any]:
    """Append a semantic task receipt and return the persisted receipt row."""

    root_path = Path(root)
    row = receipt_from_result(request, result)
    with store_lock(root_path):
        append_jsonl(semantic_task_runs_path(root_path), row)
    return row


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def list_semantic_task_runs(
    root: str | Path,
    *,
    task_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return recent semantic task receipts, newest first."""

    task_filter = str(task_type or "").strip()
    status_filter = str(status or "").strip()
    rows = _read_rows(semantic_task_runs_path(root))
    if task_filter:
        rows = [row for row in rows if str(row.get("task_type") or "") == task_filter]
    if status_filter:
        rows = [row for row in rows if str(row.get("status") or "") == status_filter]
    rows = list(reversed(rows))
    limited = rows[: max(1, int(limit))]
    return {
        "ok": True,
        "contract": SEMANTIC_TASK_RUNS_CONTRACT,
        "count": len(limited),
        "total_matching": len(rows),
        "results": limited,
    }


def _clean_bucket(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or fallback


def _inc(counter: dict[str, int], key: Any, *, fallback: str = "unknown") -> None:
    bucket = _clean_bucket(key, fallback=fallback)
    counter[bucket] = int(counter.get(bucket, 0)) + 1


def _nested_inc(counter: dict[str, dict[str, int]], outer: Any, inner: Any) -> None:
    outer_key = _clean_bucket(outer)
    inner_key = _clean_bucket(inner)
    bucket = counter.setdefault(outer_key, {})
    bucket[inner_key] = int(bucket.get(inner_key, 0)) + 1


def _metadata_runtime(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("runtime_mode") or "")


def _compact_run(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "receipt_id": str(row.get("receipt_id") or ""),
        "recorded_at": str(row.get("recorded_at") or ""),
        "task_id": str(row.get("task_id") or ""),
        "task_type": str(row.get("task_type") or ""),
        "status": str(row.get("status") or ""),
        "ok": bool(row.get("ok")),
        "model_tier": str(row.get("model_tier") or ""),
        "runtime_mode": str(metadata.get("runtime_mode") or ""),
        "prompt_version": str(row.get("prompt_version") or ""),
        "rubric_version": str(row.get("rubric_version") or ""),
        "output_schema": str(row.get("output_schema") or ""),
        "fallback_mode": str(row.get("fallback_mode") or ""),
        "authority_boundary": str(row.get("authority_boundary") or ""),
        "latency_ms": row.get("latency_ms"),
        "token_usage": dict(row.get("token_usage") or {}),
        "error": str(row.get("error") or ""),
        "result_refs": dict(row.get("result_refs") or {}),
        "metadata": metadata,
    }


def _sum_token_usage(total: dict[str, int | float], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        bucket = str(key or "unknown")
        total[bucket] = total.get(bucket, 0) + value


def _attention_row(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if not bool(row.get("ok")):
        return True
    if status in {"blocked", "blocked_by_verifier", "failed", "unavailable", "warned"}:
        return True
    return bool(str(row.get("error") or "").strip())


def summarize_semantic_task_runs(
    root: str | Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Return operator-friendly semantic task telemetry derived from receipts."""

    path = semantic_task_runs_path(root)
    rows = _read_rows(path)
    newest = list(reversed(rows))
    by_task_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_model_tier: dict[str, int] = {}
    by_runtime_mode: dict[str, int] = {}
    by_fallback_mode: dict[str, int] = {}
    by_authority_boundary: dict[str, int] = {}
    by_error: dict[str, int] = {}
    task_status_counts: dict[str, dict[str, int]] = {}
    model_tier_status_counts: dict[str, dict[str, int]] = {}
    token_usage_total: dict[str, int | float] = {}
    latency_values: list[int] = []

    for row in rows:
        task_type = row.get("task_type")
        status = row.get("status")
        model_tier = row.get("model_tier")
        runtime_mode = _metadata_runtime(row)
        _inc(by_task_type, task_type)
        _inc(by_status, status)
        _inc(by_model_tier, model_tier)
        _inc(by_runtime_mode, runtime_mode)
        _inc(by_authority_boundary, row.get("authority_boundary"))
        if str(row.get("fallback_mode") or "").strip():
            _inc(by_fallback_mode, row.get("fallback_mode"))
        if str(row.get("error") or "").strip():
            _inc(by_error, row.get("error"))
        _nested_inc(task_status_counts, task_type, status)
        _nested_inc(model_tier_status_counts, model_tier, status)
        latency = row.get("latency_ms")
        if isinstance(latency, int) and not isinstance(latency, bool):
            latency_values.append(latency)
        _sum_token_usage(token_usage_total, row.get("token_usage"))

    attention_rows = [row for row in newest if _attention_row(row)]
    attention_by_status: dict[str, int] = {}
    for row in attention_rows:
        _inc(attention_by_status, row.get("status"))
    limit_n = max(1, int(limit))
    latest = newest[0] if newest else {}
    latency_count = len(latency_values)
    latency_total = sum(latency_values)

    return {
        "ok": True,
        "contract": SEMANTIC_TASK_SUMMARY_CONTRACT,
        "generated_at": now_iso(),
        "available": path.exists(),
        "source_path": str(path),
        "total_runs": len(rows),
        "latest_run_at": str(latest.get("recorded_at") or "") if latest else None,
        "latest_run": _compact_run(latest) if latest else None,
        "counts": {
            "by_task_type": by_task_type,
            "by_status": by_status,
            "by_model_tier": by_model_tier,
            "by_runtime_mode": by_runtime_mode,
            "by_fallback_mode": by_fallback_mode,
            "by_authority_boundary": by_authority_boundary,
            "task_status_counts": task_status_counts,
            "model_tier_status_counts": model_tier_status_counts,
        },
        "attention": {
            "count": len(attention_rows),
            "by_status": attention_by_status,
            "recent": [_compact_run(row) for row in attention_rows[:limit_n]],
        },
        "errors": {
            "count": sum(by_error.values()),
            "by_error": by_error,
        },
        "latency_ms": {
            "count": latency_count,
            "avg": int(latency_total / latency_count) if latency_count else None,
            "max": max(latency_values) if latency_values else None,
        },
        "token_usage": {
            "total": token_usage_total,
        },
        "recent": [_compact_run(row) for row in newest[:limit_n]],
    }
