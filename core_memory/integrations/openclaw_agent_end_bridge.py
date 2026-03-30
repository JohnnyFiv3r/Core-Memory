from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

from core_memory.integrations.openclaw_runtime import finalize_and_process_turn
from core_memory.persistence.store import DEFAULT_ROOT

ADAPTER_KIND = "bridge"
ADAPTER_RUNTIME = "openclaw"
ADAPTER_STATUS = "production_bridge"


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = str(item.get("text") or "").strip()
                if txt:
                    parts.append(txt)
        return "\n".join(parts).strip()
    return ""


def _last_role_message(messages: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    for m in reversed(messages):
        if str(m.get("role") or "").strip().lower() == role.lower():
            return m
    return None


def _extract_from_key(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if value is None:
        return ""
    if isinstance(value, dict):
        for nested in ("content", "text", "message", "output", "response"):
            if nested in value:
                txt = _extract_text(value.get(nested))
                if txt:
                    return txt
        return ""
    return _extract_text(value)


def _fallback_text(event: dict[str, Any], ctx: dict[str, Any], role: str) -> str:
    if role == "user":
        candidates = [
            "user",
            "user_text",
            "userMessage",
            "prompt",
            "input",
            "query",
            "message",
            "lastUserMessage",
        ]
    else:
        candidates = [
            "assistant",
            "assistant_text",
            "assistantMessage",
            "response",
            "output",
            "reply",
            "final",
            "lastAssistantMessage",
        ]

    for key in candidates:
        txt = _extract_from_key(event, key)
        if txt:
            return txt

    # Try nested objects commonly used by wrappers.
    for container_key in ("result", "data", "payload"):
        container = event.get(container_key)
        if isinstance(container, dict):
            for key in candidates:
                txt = _extract_from_key(container, key)
                if txt:
                    return txt

    # Context fallback for user-side prompt text.
    if role == "user":
        for ctx_key in ("userMessage", "prompt", "query", "message"):
            txt = _extract_from_key(ctx, ctx_key)
            if txt:
                return txt

    return ""


def _first_nonempty(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _extract_session_id(event: dict[str, Any], ctx: dict[str, Any]) -> str:
    session_obj = event.get("session") if isinstance(event.get("session"), dict) else {}
    context_obj = event.get("context") if isinstance(event.get("context"), dict) else {}
    return _first_nonempty(
        ctx.get("sessionId"),
        ctx.get("sessionKey"),
        event.get("sessionKey"),
        event.get("sessionId"),
        event.get("session_id"),
        session_obj.get("key"),
        session_obj.get("id"),
        context_obj.get("sessionKey"),
        context_obj.get("sessionId"),
        "main",
    )


def _extract_trace_list(event: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    for key in keys:
        value = event.get(key)
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"value": item} for item in value]
    result = event.get("result")
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"value": item} for item in value]
    return []


def _stable_turn_id(session_id: str, user_query: str, assistant_final: str, seed: str = "") -> str:
    h = hashlib.sha256()
    h.update(session_id.encode("utf-8", "ignore"))
    h.update(b"\n")
    h.update(user_query.encode("utf-8", "ignore"))
    h.update(b"\n")
    h.update(assistant_final.encode("utf-8", "ignore"))
    if seed:
        h.update(b"\n")
        h.update(seed.encode("utf-8", "ignore"))
    return f"turn-{h.hexdigest()[:16]}"


def _state_file(root: str) -> Path:
    return Path(root) / ".beads" / "events" / "agent-end-bridge-state.json"


def _load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {str(k): str(v) for k, v in payload.items()}
    except Exception:
        pass
    return {}


def _save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def process_agent_end_event(
    *,
    event: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    root: str | None = None,
) -> dict[str, Any]:
    """Thin bridge: extract -> dedupe -> finalize_and_process_turn -> return."""
    ctx = dict(ctx or {})
    root_final = str(root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT)

    # Recursion guard: memory-origin runs should not re-emit.
    trigger = str(ctx.get("trigger") or "").strip().lower()
    if trigger == "memory":
        return {"ok": True, "emitted": False, "reason": "memory_trigger_skip"}
    if str((event.get("metadata") or {}).get("origin") or "") == "MEMORY_PASS":
        return {"ok": True, "emitted": False, "reason": "memory_origin_skip"}

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

    # Some hooks provide only assistant output. Keep ingestion alive with a placeholder.
    if not user_query and assistant_final:
        user_query = "[agent_end:auto:user_missing]"

    if not assistant_final:
        return {"ok": True, "emitted": False, "reason": "missing_assistant_output"}

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
    dedupe_key = f"{session_id}:{turn_id}"

    sf = _state_file(root_final)
    state = _load_state(sf)
    if state.get(dedupe_key) == "emitted":
        return {
            "ok": True,
            "emitted": False,
            "reason": "deduped",
            "session_id": session_id,
            "turn_id": turn_id,
        }

    transaction_id = str(run_id or f"tx-{turn_id}-{uuid.uuid4().hex[:6]}")
    trace_id = str(run_id or f"tr-{turn_id}-{uuid.uuid4().hex[:6]}")

    md = {
        "bridge": "openclaw_agent_end",
        "sessionKey": ctx.get("sessionKey"),
        "agentId": ctx.get("agentId"),
        "success": bool(event.get("success")),
        "error": event.get("error"),
        "durationMs": event.get("durationMs"),
    }

    tools_trace = _extract_trace_list(event, ("tools_trace", "toolsTrace", "tool_trace", "toolTrace", "tools"))
    mesh_trace = _extract_trace_list(event, ("mesh_trace", "meshTrace"))

    out = finalize_and_process_turn(
        root=root_final,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        origin="USER_TURN",
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        metadata=md,
    )

    if out.get("ok"):
        event_id = str((((out.get("emitted") or {}).get("payload") or {}).get("event") or {}).get("event_id") or "")
        state[dedupe_key] = "emitted"
        _save_state(sf, state)
        return {
            "ok": True,
            "emitted": bool((out.get("emitted") or {}).get("emitted", False)),
            "event_id": event_id,
            "processed": int(out.get("processed", 0) or 0),
            "failed": int(out.get("failed", 0) or 0),
            "session_id": session_id,
            "turn_id": turn_id,
            "result": out,
        }

    state[dedupe_key] = "failed"
    _save_state(sf, state)
    return {
        "ok": False,
        "emitted": bool((out.get("emitted") or {}).get("emitted", False)),
        "error": str(out.get("error") or "finalize_process_failed"),
        "session_id": session_id,
        "turn_id": turn_id,
        "result": out,
    }


def main() -> None:
    """CLI bridge. Reads JSON from stdin: {"event": {...}, "ctx": {...}}."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "ignore").strip()
        if not raw:
            print(json.dumps({"ok": False, "error": "missing_input"}))
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            print(json.dumps({"ok": False, "error": "invalid_input"}))
            return

        # Accept either nested or flat payload forms.
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else payload.get("context")
        root = payload.get("root")

        out = process_agent_end_event(event=event, ctx=ctx, root=str(root) if root else None)
        print(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"bridge_exception:{exc}",
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
