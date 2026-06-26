from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any


T1_ADAPTER_REQUEST_SCHEMA = "causal_continuity.t1_adapter_request.v1"
T1_ADAPTER_RESPONSE_SCHEMA = "causal_continuity.t1_adapter_response.v1"


class CommandAdapterError(RuntimeError):
    """Raised when a configured benchmark command adapter cannot produce ranks."""


def _command_args(command: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command if str(part).strip()]
    return shlex.split(str(command or ""))


def _timeout_seconds(timeout_s: float | None = None) -> float:
    if timeout_s is not None:
        return max(0.1, float(timeout_s))
    raw = str(os.environ.get("CORE_MEMORY_BENCHMARK_ADAPTER_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(0.1, float(raw))
        except ValueError:
            raise CommandAdapterError("adapter_timeout_invalid")
    return 30.0


def run_t1_command_adapter(
    *,
    command: str | list[str] | tuple[str, ...],
    request: dict[str, Any],
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Run a configured T1 comparator adapter.

    The command protocol is intentionally small and source-agnostic: JSON request
    on stdin, JSON response on stdout. The harness owns scoring and faithfulness
    flags; the adapter only supplies a ranked list of fixture document keys.
    """

    args = _command_args(command)
    if not args:
        raise CommandAdapterError("adapter_command_not_configured")
    try:
        completed = subprocess.run(
            args,
            input=json.dumps(request, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=_timeout_seconds(timeout_s),
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandAdapterError(f"adapter_command_not_found:{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandAdapterError("adapter_command_timeout") from exc

    if completed.returncode != 0:
        stderr = " ".join(str(completed.stderr or "").strip().split())
        suffix = f":{stderr[:240]}" if stderr else ""
        raise CommandAdapterError(f"adapter_command_failed:{completed.returncode}{suffix}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CommandAdapterError("adapter_command_invalid_json") from exc
    if not isinstance(payload, dict):
        raise CommandAdapterError("adapter_command_response_not_object")
    return payload


def normalize_t1_adapter_response(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "completed").strip().lower()
    if status not in {"completed", "ok", "success"}:
        raise CommandAdapterError(f"adapter_response_status:{status or 'empty'}")

    raw_ranked = payload.get("ranked_keys")
    if raw_ranked is None:
        raw_ranked = payload.get("results")
    if raw_ranked is None:
        raw_ranked = payload.get("ranked")
    if not isinstance(raw_ranked, list):
        raise CommandAdapterError("adapter_response_missing_ranked_keys")

    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_ranked, start=1):
        if isinstance(item, str):
            key = item.strip()
            score_raw: Any = None
            reason = ""
        elif isinstance(item, dict):
            key = str(item.get("key") or item.get("document_key") or item.get("id") or "").strip()
            score_raw = item.get("score")
            reason = str(item.get("reason") or item.get("rationale") or "")
        else:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            score = float(score_raw) if score_raw is not None else 1.0 / float(idx)
        except (TypeError, ValueError):
            score = 1.0 / float(idx)
        ranked.append({
            "key": key,
            "score": score,
            "rank": len(ranked) + 1,
            "reason": reason,
        })

    if not ranked:
        raise CommandAdapterError("adapter_response_empty_ranked_keys")

    warnings = [str(w) for w in (payload.get("warnings") or []) if str(w).strip()]
    return {
        "schema_version": str(payload.get("schema_version") or T1_ADAPTER_RESPONSE_SCHEMA),
        "status": "completed",
        "adapter_name": str(payload.get("adapter_name") or "command_adapter"),
        "ranked_keys": ranked,
        "warnings": warnings,
    }
