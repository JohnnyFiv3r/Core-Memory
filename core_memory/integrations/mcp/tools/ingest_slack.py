"""MCP ingest adapter for Slack JSON exports (#10B).

Accepts Slack workspace data export format (channels/*.json) or Slack API
messages-list responses. Normalises to the canonical group-mode turn envelope
with ``metadata.source_system = "slack"`` and calls ``ingest_transcript()``.

Slack user IDs (``U12345ABCDE``) are stable workspace-scoped identifiers and
are used as the speaker label so ``resolve_speaker()`` can merge across sessions.
Display names (``username``) are included as aliases when present.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.transcript_ingest import ingest_transcript


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def _parse_message(row: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    msg_type = str(row.get("type") or "").strip().lower()
    if msg_type and msg_type not in {"message", ""}:
        return None
    content = str(row.get("text") or row.get("content") or "").strip()
    if not content:
        return None
    user_id = str(row.get("user") or row.get("user_id") or "").strip()
    username = str(row.get("username") or row.get("name") or "").strip()
    # Prefer stable user ID; fall back to display name
    speaker = user_id or username or f"slack-user-{index}"
    ts_raw = row.get("ts") or row.get("timestamp") or ""
    return {
        "speaker": speaker,
        "role": "user",
        "content": content,
        "ts": str(ts_raw) if ts_raw else None,
        "metadata": {"slack_user_id": user_id, "slack_username": username} if user_id else {},
    }


def _parse_slack_payload(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("messages") or value.get("turns") or []
    else:
        return []
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        msg = _parse_message(row, i)
        if msg is not None:
            out.append(msg)
    return out


def ingest_slack_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ingest a Slack message export into Core Memory.

    Payload fields:
    - ``path``: path to a Slack channel export JSON file (optional)
    - ``messages``: inline list of Slack message objects (optional)
    - ``channel_id``: Slack channel identifier — used to scope the session
    - ``transcript_id``: override transcript identifier
    - ``session_id``: override session identifier
    - ``root``: Core Memory root path (default ".")
    - ``flush_policy``: "none" | "end_only" | "per_session"
    - ``window_size``: utterances per envelope (default 10)
    """
    payload = dict(payload or {})
    raw_path = str(payload.get("path") or "").strip()
    inline = payload.get("messages") or payload.get("turns")
    channel_id = str(payload.get("channel_id") or "").strip()

    turns: list[dict[str, Any]] = []

    if inline is not None:
        raw = inline if isinstance(inline, list) else []
        turns = _parse_slack_payload(raw)
    elif raw_path:
        p = Path(raw_path).expanduser()
        if not p.exists() or not p.is_file():
            return _error("cm.path_not_readable", "slack ingest path is not a readable file", path=raw_path)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _error("cm.parser_aborted", str(exc), path=raw_path)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            return _error("cm.parser_aborted", f"invalid JSON: {exc}", path=raw_path)
        turns = _parse_slack_payload(value)
    else:
        return _error("cm.path_not_readable", "ingest_slack requires 'path' or 'messages'")

    if not turns:
        return _error("cm.parser_aborted", "no usable messages found in slack export")

    transcript_id = str(payload.get("transcript_id") or channel_id or (Path(raw_path).stem if raw_path else "slack"))
    session_id = str(payload.get("session_id") or f"slack:{transcript_id}")

    try:
        out = ingest_transcript(
            root=str(payload.get("root") or "."),
            transcript_id=transcript_id,
            session_id=session_id,
            turns=turns,
            flush_policy=str(payload.get("flush_policy") or "none"),
            metadata={"source_system": "slack", "channel_id": channel_id},
            max_turns=int(payload.get("max_turns") or 500),
            mode="group",
            window_size=int(payload.get("window_size") or 10),
        )
    except ValueError as exc:
        return _error("cm.parser_aborted", str(exc))

    if not out.get("ok"):
        return _error("cm.ingest_failed", "slack transcript ingest failed", errors=out.get("errors"))

    bead_ids: list[str] = []
    for row in out.get("ingested") or []:
        if isinstance(row, dict):
            bead_ids.extend(str(v) for v in row.get("bead_ids") or [] if str(v))

    return {
        "ok": True,
        "source_system": "slack",
        "channel_id": channel_id,
        "session_id": out.get("session_id"),
        "turns_ingested": out.get("turns_received"),
        "turns_paired": out.get("turns_paired"),
        "bead_ids": list(dict.fromkeys(bead_ids)),
        "raw": out,
    }
