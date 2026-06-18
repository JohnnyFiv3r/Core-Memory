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
