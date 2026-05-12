"""MCP `recall` tool wrapper."""

from __future__ import annotations

from typing import Any

from core_memory.retrieval.agent import recall
from core_memory.retrieval.contracts import validate_recall_effort


def _speaker_arg(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip()) or None
    text = str(value).strip()
    return text or None


def _invalid_request(message: str, *, field: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "cm.invalid_request",
            "message": message,
            "data": {"tool": "recall", "field": field},
        },
    }


def recall_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    query = str(payload.get("query") or "").strip()
    if not query:
        return _invalid_request("recall.query is required", field="query")
    try:
        effort = validate_recall_effort(str(payload.get("effort") or "medium"))
    except ValueError as exc:
        return _invalid_request(str(exc), field="effort")
    if effort == "dynamic":
        return _invalid_request("effort='dynamic' is reserved; use low, medium, or high", field="effort")
    result = recall(
        query,
        effort=effort,
        speaker=_speaker_arg(payload.get("speaker")),
        root=str(payload.get("root") or "."),
        include_raw=bool(payload.get("include_raw", False)),
    )
    out = result.to_dict()
    out["ok"] = result.status not in {"failed"}
    if result.status == "empty":
        out.setdefault("warnings", []).append("recall returned no grounded evidence")
    return out
