"""MCP `ingest` tool wrapper."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.integrations.mcp.tools.capture import capture_handler

_ROLE_MAP = {
    "user": "user",
    "human": "user",
    "customer": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "agent": "assistant",
    "model": "assistant",
    "system": "other",
    "tool": "other",
    "other": "other",
}
_SUPPORTED_FORMATS = {"auto", "json", "jsonl", "markdown", "text"}
_MARKDOWN_SPEAKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(user|human|assistant|ai|agent|system|tool|other)\s*:\s*(.*)$",
    re.I,
)


def _error(
    code: str,
    message: str,
    *,
    field: str = "",
    received: Any = None,
    malformed: list[Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"tool": "ingest"}
    if field:
        data["field"] = field
    if received is not None:
        data["received"] = received
    if malformed:
        data["malformed"] = malformed[:20]
    return {"ok": False, "error": {"code": code, "message": message, "data": data}}


def _role(value: Any) -> str:
    return _ROLE_MAP.get(str(value or "").strip().lower(), "other")


def _speaker(value: Any, role: str) -> str:
    text = str(value or "").strip()
    if text:
        return text
    if role == "user":
        return "user"
    if role == "assistant":
        return "assistant"
    return "other"


def _coerce_message(row: Any, *, index: int, malformed: list[Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        malformed.append({"index": index, "reason": "not_object"})
        return None
    content = row.get("content")
    if content is None:
        content = row.get("text") or row.get("message") or row.get("body")
    content = str(content or "")
    if not content.strip():
        malformed.append({"index": index, "reason": "missing_content"})
        return None
    raw_role = row.get("role") or row.get("type") or row.get("speaker") or row.get("name")
    role = _role(raw_role)
    speaker = _speaker(row.get("speaker") or row.get("name") or raw_role, role)
    turn: dict[str, Any] = {"speaker": speaker, "role": role, "content": content}
    if row.get("ts") is not None:
        turn["ts"] = row.get("ts")
    elif row.get("timestamp") is not None:
        turn["ts"] = row.get("timestamp")
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        turn["metadata"] = metadata
    return turn


def _messages_from_json(value: Any, malformed: list[Any]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("turns"), list):
            rows = value["turns"]
        elif isinstance(value.get("messages"), list):
            rows = value["messages"]
        elif isinstance(value.get("conversation"), list):
            rows = value["conversation"]
        elif "user" in value or "assistant" in value:
            rows = [
                {
                    "role": "user",
                    "speaker": value.get("as_user") or "user",
                    "content": value.get("user") or "",
                },
                {
                    "role": "assistant",
                    "speaker": value.get("as_assistant") or "assistant",
                    "content": value.get("assistant") or "",
                },
            ]
        else:
            malformed.append({"index": 0, "reason": "missing_turns_or_messages"})
            return []
    elif isinstance(value, list):
        rows = value
    else:
        malformed.append({"index": 0, "reason": "json_root_not_object_or_array"})
        return []
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        msg = _coerce_message(row, index=i, malformed=malformed)
        if msg is not None:
            out.append(msg)
    return out


def _parse_json(text: str, malformed: list[Any]) -> list[dict[str, Any]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        malformed.append({"line": exc.lineno, "column": exc.colno, "reason": "invalid_json"})
        return []
    return _messages_from_json(value, malformed)


def _parse_jsonl(text: str, malformed: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed.append({"line": line_no, "reason": "invalid_json"})
            continue
        msg = _coerce_message(row, index=line_no, malformed=malformed)
        if msg is not None:
            out.append(msg)
    return out


def _parse_markdown(text: str, malformed: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current_role = ""
    current_speaker = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_speaker, current_lines
        content = "\n".join(current_lines).strip()
        if current_role and content:
            out.append({"speaker": current_speaker, "role": current_role, "content": content})
        current_role = ""
        current_speaker = ""
        current_lines = []

    for line in text.splitlines():
        match = _MARKDOWN_SPEAKER_RE.match(line)
        if match:
            flush()
            label, rest = match.groups()
            current_role = _role(label)
            current_speaker = _speaker(label.lower(), current_role)
            current_lines = [rest] if rest else []
        elif current_role:
            current_lines.append(line)
    flush()
    if not out:
        malformed.append({"reason": "no_speaker_blocks"})
    return out


def _detect_format(path: Path, text: str, requested: str) -> str:
    if requested != "auto":
        return requested
    suffix = path.suffix.lower()
    stripped = text.lstrip()
    if suffix == ".json" or stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    if suffix in {".md", ".markdown", ".txt", ".text"}:
        return "markdown"
    return "markdown"


def ingest_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        return _error("cm.path_not_readable", "ingest requires a readable local path", field="path")
    path = Path(raw_path).expanduser()
    if not path.exists() or not path.is_file():
        return _error("cm.path_not_readable", "ingest path is not a readable file", field="path", received=raw_path)

    requested = str(payload.get("from") or payload.get("format") or "auto").strip().lower()
    if requested not in _SUPPORTED_FORMATS:
        return _error("cm.parser_format_unsupported", "unsupported ingest format", field="from", received=requested)

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error("cm.parser_aborted", "ingest file is not valid UTF-8", field="path", received=raw_path)
    except OSError as exc:
        return _error("cm.path_not_readable", str(exc), field="path", received=raw_path)

    fmt = _detect_format(path, text, requested)
    malformed: list[Any] = []
    if fmt == "json":
        turns = _parse_json(text, malformed)
    elif fmt == "jsonl":
        turns = _parse_jsonl(text, malformed)
    elif fmt in {"markdown", "text"}:
        turns = _parse_markdown(text, malformed)
    else:
        return _error("cm.parser_format_unsupported", "unsupported ingest format", field="from", received=fmt)

    has_user = any(t.get("role") == "user" for t in turns)
    has_assistant = any(t.get("role") == "assistant" for t in turns)
    if not turns or not (has_user and has_assistant):
        return _error(
            "cm.parser_aborted",
            "transcript lacks usable user/assistant turn structure",
            field="path",
            received=raw_path,
            malformed=malformed,
        )

    session_prefix = str(payload.get("session_prefix") or "ingest").strip() or "ingest"
    session_id = str(payload.get("session_id") or f"{session_prefix}:{path.stem}")
    turn_id = str(payload.get("turn_id") or f"ingest:{path.stem}")
    capture = capture_handler(
        {
            "root": payload.get("root") or ".",
            "session_id": session_id,
            "turn_id": turn_id,
            "turns": turns,
        }
    )
    if not capture.get("ok"):
        return capture
    return {
        "ok": True,
        "path": str(path),
        "format": fmt,
        "session_id": capture.get("session_id"),
        "turn_id": capture.get("turn_id"),
        "turns_ingested": len(turns),
        "malformed_count": len(malformed),
        "malformed": malformed[:20],
        "bead_ids": capture.get("bead_ids", []),
        "raw": capture.get("raw", {}),
    }
