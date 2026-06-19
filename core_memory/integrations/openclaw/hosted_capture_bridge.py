from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core_memory.integrations.openclaw.agent_end_bridge import (
    ADAPTER_KIND,
    ADAPTER_RUNTIME,
    ADAPTER_STATUS,
    _extract_session_id,
    _extract_text,
    _extract_trace_list,
    _fallback_text,
    _first_nonempty,
    _last_role_message,
    _load_state,
    _save_state,
    _stable_turn_id,
)

DEFAULT_STATE_PATH = "/tmp/core-memory-openclaw-hosted-state.json"


def _env_enabled(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _hosted_config(payload: dict[str, Any]) -> dict[str, Any]:
    hosted = payload.get("hosted") if isinstance(payload.get("hosted"), dict) else {}
    return {
        "url": _first_nonempty(
            hosted.get("url"),
            hosted.get("coreMemoryUrl"),
            os.environ.get("SATORID_OPENCLAW_CORE_MEMORY_URL"),
            os.environ.get("CORE_MEMORY_HOSTED_TURN_FINALIZED_URL"),
            os.environ.get("CORE_MEMORY_HOSTED_API_BASE_URL"),
        ),
        "token": _first_nonempty(
            hosted.get("token"),
            hosted.get("coreMemoryToken"),
            os.environ.get("SATORID_GATEWAY_KEY"),
            os.environ.get("SATORID_CORE_MEMORY_HTTP_TOKEN"),
            os.environ.get("CORE_MEMORY_HOSTED_HTTP_TOKEN"),
        ),
        "tenant_id": _first_nonempty(
            hosted.get("tenantId"),
            hosted.get("tenant_id"),
            os.environ.get("SATORID_CORE_MEMORY_TENANT_ID"),
            os.environ.get("CORE_MEMORY_HOSTED_TENANT_ID"),
        ),
        "state_path": _first_nonempty(
            hosted.get("statePath"),
            hosted.get("state_path"),
            os.environ.get("CORE_MEMORY_OPENCLAW_HOSTED_STATE_PATH"),
            DEFAULT_STATE_PATH,
        ),
        "timeout": float(hosted.get("timeout") or os.environ.get("CORE_MEMORY_HOSTED_HTTP_TIMEOUT") or 12),
        "enabled": _env_enabled(
            hosted.get("enabled", os.environ.get("CORE_MEMORY_BRIDGE_ENABLE_HOSTED_CLONE")),
            default=True,
        ),
    }


def _turn_finalized_url(raw_url: str) -> str:
    clean = raw_url.strip().rstrip("/")
    if clean.endswith("/turn-finalized"):
        return clean
    return f"{clean}/v1/memory/turn-finalized"


def _extract_turn(event: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    messages = event.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    last_user = _last_role_message(messages, "user") if messages else None
    last_assistant = _last_role_message(messages, "assistant") if messages else None

    user_query = _extract_text((last_user or {}).get("content"))
    assistant_final = _extract_text((last_assistant or {}).get("content"))

    if not user_query:
        user_query = _fallback_text(event, ctx, "user")
    if not assistant_final:
        assistant_final = _fallback_text(event, ctx, "assistant")

    if not user_query and assistant_final:
        user_query = "[agent_end:auto:user_missing]"

    session_id = _extract_session_id(event, ctx)
    result_obj = event.get("result") if isinstance(event.get("result"), dict) else {}
    run_id = str(
        event.get("runId")
        or event.get("id")
        or result_obj.get("runId")
        or ctx.get("runId")
        or ctx.get("turnId")
        or ""
    )
    turn_id = _stable_turn_id(session_id, user_query, assistant_final, seed=run_id)
    transaction_id = str(run_id or f"tx-{turn_id}")
    trace_id = str(run_id or f"tr-{turn_id}")

    return {
        "user_query": user_query,
        "assistant_final": assistant_final,
        "session_id": session_id,
        "run_id": run_id,
        "turn_id": turn_id,
        "transaction_id": transaction_id,
        "trace_id": trace_id,
    }


def _build_http_payload(event: dict[str, Any], ctx: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    tools_trace = _extract_trace_list(event, ("tools_trace", "toolsTrace", "tool_trace", "toolTrace", "tools"))
    mesh_trace = _extract_trace_list(event, ("mesh_trace", "meshTrace"))
    return {
        "session_id": turn["session_id"],
        "turn_id": turn["turn_id"],
        "transaction_id": turn["transaction_id"],
        "trace_id": turn["trace_id"],
        "turns": [
            {"speaker": "user", "role": "user", "content": turn["user_query"]},
            {"speaker": "assistant", "role": "assistant", "content": turn["assistant_final"]},
        ],
        "origin": "OPENCLAW_HOSTED_CLONE",
        "metadata": {
            **metadata,
            "framework": ADAPTER_RUNTIME,
            "source": "openclaw_hosted_capture_bridge",
            "adapter_kind": ADAPTER_KIND,
            "adapter_status": ADAPTER_STATUS,
            "sessionKey": ctx.get("sessionKey"),
            "agentId": ctx.get("agentId"),
            "success": bool(event.get("success")),
            "error": event.get("error"),
            "durationMs": event.get("durationMs"),
            "openclaw_session_key": turn["session_id"],
            "openclaw_run_id": turn["run_id"],
            "local_core_memory_bypassed": True,
            "bead_judge": "llm",
        },
        "traces": {
            "tools": tools_trace,
            "mesh": mesh_trace,
        },
    }


def _post_json(url: str, payload: dict[str, Any], *, token: str = "", tenant_id: str = "", timeout: float = 12) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Memory-Token"] = token
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - operator configured endpoint
        raw = resp.read().decode("utf-8", "ignore")
        parsed = json.loads(raw) if raw.strip() else {}
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
        parsed["_http_status"] = int(getattr(resp, "status", 200) or 200)
        return parsed


def process_hosted_capture_event(
    *,
    event: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    hosted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(ctx or {})
    cfg = _hosted_config({"hosted": hosted or {}})

    if not cfg["enabled"]:
        return {"ok": True, "emitted": False, "reason": "hosted_clone_disabled"}

    if not cfg["url"]:
        return {"ok": True, "emitted": False, "reason": "missing_hosted_url"}

    trigger = str(ctx.get("trigger") or "").strip().lower()
    if trigger == "memory":
        return {"ok": True, "emitted": False, "reason": "memory_trigger_skip"}
    if str((event.get("metadata") or {}).get("origin") or "") == "MEMORY_PASS":
        return {"ok": True, "emitted": False, "reason": "memory_origin_skip"}

    turn = _extract_turn(event, ctx)
    if not turn["assistant_final"]:
        return {"ok": True, "emitted": False, "reason": "missing_assistant_output"}

    dedupe_key = f"{turn['session_id']}:{turn['turn_id']}"
    state_path = Path(str(cfg["state_path"]))
    state = _load_state(state_path)
    if state.get(dedupe_key) == "emitted":
        return {
            "ok": True,
            "emitted": False,
            "reason": "deduped",
            "session_id": turn["session_id"],
            "turn_id": turn["turn_id"],
        }

    endpoint = _turn_finalized_url(str(cfg["url"]))
    payload = _build_http_payload(event, ctx, turn)

    try:
        receipt = _post_json(
            endpoint,
            payload,
            token=str(cfg["token"] or ""),
            tenant_id=str(cfg["tenant_id"] or ""),
            timeout=float(cfg["timeout"] or 12),
        )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            body: Any = json.loads(raw) if raw.strip() else {}
        except Exception:
            body = {"body": raw}
        return {
            "ok": False,
            "emitted": False,
            "reason": "hosted_http_error",
            "http_status": int(exc.code),
            "session_id": turn["session_id"],
            "turn_id": turn["turn_id"],
            "error": body,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "emitted": False,
            "reason": "hosted_post_failed",
            "session_id": turn["session_id"],
            "turn_id": turn["turn_id"],
            "error": str(exc),
        }

    accepted = receipt.get("accepted") is not False and receipt.get("ok") is not False
    if accepted:
        state[dedupe_key] = "emitted"
        _save_state(state_path, state)

    return {
        "ok": bool(accepted),
        "emitted": bool(accepted),
        "reason": "" if accepted else "hosted_rejected",
        "event_id": str(receipt.get("event_id") or ""),
        "processed": int(receipt.get("processed") or 0),
        "failed": 0 if accepted else 1,
        "http_status": int(receipt.get("_http_status") or 200),
        "session_id": turn["session_id"],
        "turn_id": turn["turn_id"],
        "receipt": receipt,
    }


def main() -> None:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "ignore").strip()
        if not raw:
            print(json.dumps({"ok": False, "error": "missing_input"}))
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            print(json.dumps({"ok": False, "error": "invalid_input"}))
            return

        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else payload.get("context")
        hosted = payload.get("hosted") if isinstance(payload.get("hosted"), dict) else None

        out = process_hosted_capture_event(event=event, ctx=ctx, hosted=hosted)
        print(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"hosted_bridge_exception:{exc}",
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
