"""MCP `capture` tool wrapper."""

from __future__ import annotations

from typing import Any

from core_memory.memory import Memory


def _error(code: str, message: str, *, field: str = "", received: Any = None) -> dict[str, Any]:
    data: dict[str, Any] = {"tool": "capture"}
    if field:
        data["field"] = field
    if received is not None:
        data["received"] = received
    return {"ok": False, "error": {"code": code, "message": message, "data": data}}


def _bead_ids_from_result(result: dict[str, Any]) -> list[str]:
    bead_ids: list[str] = []
    for key in ("bead_ids", "created_bead_ids", "updated_bead_ids"):
        value = result.get(key)
        if isinstance(value, list):
            bead_ids.extend(str(v) for v in value if str(v))
    created = result.get("created")
    if isinstance(created, list):
        for row in created:
            if isinstance(row, dict) and row.get("bead_id"):
                bead_ids.append(str(row["bead_id"]))
    if result.get("bead_id"):
        bead_ids.append(str(result["bead_id"]))
    return list(dict.fromkeys(bead_ids))


def capture_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    root = str(payload.get("root") or ".")
    session_id = str(payload.get("session_id") or "default")
    turn_id = str(payload.get("turn_id") or "") or None
    turns = payload.get("turns")
    has_shortcut = any(payload.get(k) is not None for k in ("user", "assistant", "as_user", "as_assistant"))

    if turns is not None and has_shortcut:
        return _error(
            "cm.invalid_turn",
            "capture accepts either turns or user/assistant shortcut, not both",
            field="turns",
        )
    if turns is None and not has_shortcut:
        return _error("cm.invalid_turn", "capture requires turns or user/assistant shortcut", field="turns")
    if turns is not None and not isinstance(turns, list):
        return _error("cm.invalid_turn", "capture.turns must be a list", field="turns", received=type(turns).__name__)

    try:
        memory = Memory(root=root)
        if turns is not None:
            result = memory.capture(turns=turns, session_id=session_id, turn_id=turn_id)
        else:
            result = memory.capture(
                user=payload.get("user"),
                assistant=payload.get("assistant"),
                as_user=payload.get("as_user"),
                as_assistant=payload.get("as_assistant"),
                session_id=session_id,
                turn_id=turn_id,
            )
    except ValueError as exc:
        return _error("cm.invalid_turn", str(exc))
    except FileNotFoundError as exc:
        return _error("cm.store_not_found", str(exc))

    result = dict(result or {})
    return {
        "ok": bool(result.get("ok", True)),
        "session_id": str(result.get("session_id") or session_id),
        "turn_id": str(result.get("turn_id") or turn_id or ""),
        "bead_ids": _bead_ids_from_result(result),
        "raw": result,
    }
