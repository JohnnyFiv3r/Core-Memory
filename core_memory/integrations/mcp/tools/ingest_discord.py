"""MCP ingest adapter for Discord JSON exports (#10B).

Accepts DiscordChatExporter JSON format (the most common open-source export
tool) and normalises to the canonical group-mode turn envelope with
``metadata.source_system = "discord"``.

Discord user IDs (snowflake integers) are stable identifiers. The adapter
records ``author.username`` (without the legacy ``#discriminator`` suffix
that ``resolve_speaker()`` already strips) as the speaker label.
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
    content = str(row.get("content") or row.get("text") or "").strip()
    if not content:
        return None
    msg_type = str(row.get("type") or "Default").strip()
    # Skip system message types
    if msg_type.lower() in {"call", "channelnamechange", "channelpinnedmessage", "guildmemberjoin"}:
        return None

    author = row.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    username = str(author.get("username") or author.get("name") or "").strip()
    discriminator = str(author.get("discriminator") or "").strip()
    author_id = str(author.get("id") or "").strip()
    nickname = str(author.get("nickname") or "").strip()
    # Use stable author_id as the canonical speaker label so renames don't
    # create new entities. Fall back to username only when no id is present.
    if author_id:
        speaker = f"discord:{author_id}"
    else:
        speaker = username or f"discord-user-{index}"

    ts = str(row.get("timestamp") or row.get("ts") or "").strip()
    return {
        "speaker": speaker,
        "role": "user",
        "content": content,
        "ts": ts or None,
        "metadata": {
            "discord_author_id": author_id,
            "discord_username": username,
            "discord_nickname": nickname,
        },
    }


def _parse_discord_payload(value: Any) -> list[dict[str, Any]]:
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


def ingest_discord_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ingest a Discord message export into Core Memory.

    Payload fields:
    - ``path``: path to a DiscordChatExporter JSON file (optional)
    - ``messages``: inline list of Discord message objects (optional)
    - ``channel_id``: Discord channel snowflake — used to scope the session
    - ``guild_id``: Discord guild/server snowflake (optional, stored in metadata)
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
    guild_id = str(payload.get("guild_id") or "").strip()

    turns: list[dict[str, Any]] = []

    if inline is not None:
        raw = inline if isinstance(inline, list) else []
        turns = _parse_discord_payload(raw)
    elif raw_path:
        p = Path(raw_path).expanduser()
        if not p.exists() or not p.is_file():
            return _error("cm.path_not_readable", "discord ingest path is not a readable file", path=raw_path)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _error("cm.parser_aborted", str(exc), path=raw_path)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            return _error("cm.parser_aborted", f"invalid JSON: {exc}", path=raw_path)
        # DiscordChatExporter wraps in a top-level object
        if isinstance(value, dict) and not channel_id:
            channel_info = value.get("channel") or {}
            if isinstance(channel_info, dict):
                channel_id = str(channel_info.get("id") or "").strip()
        turns = _parse_discord_payload(value)
    else:
        return _error("cm.path_not_readable", "ingest_discord requires 'path' or 'messages'")

    if not turns:
        return _error("cm.parser_aborted", "no usable messages found in discord export")

    transcript_id = str(
        payload.get("transcript_id") or channel_id or (Path(raw_path).stem if raw_path else "discord")
    )
    session_id = str(payload.get("session_id") or f"discord:{transcript_id}")

    extra_meta: dict[str, Any] = {"source_system": "discord"}
    if channel_id:
        extra_meta["channel_id"] = channel_id
    if guild_id:
        extra_meta["guild_id"] = guild_id

    try:
        out = ingest_transcript(
            root=str(payload.get("root") or "."),
            transcript_id=transcript_id,
            session_id=session_id,
            turns=turns,
            flush_policy=str(payload.get("flush_policy") or "none"),
            metadata=extra_meta,
            max_turns=int(payload.get("max_turns") or 500),
            mode="group",
            window_size=int(payload.get("window_size") or 10),
        )
    except ValueError as exc:
        return _error("cm.parser_aborted", str(exc))

    if not out.get("ok"):
        return _error("cm.ingest_failed", "discord transcript ingest failed", errors=out.get("errors"))

    bead_ids: list[str] = []
    for row in out.get("ingested") or []:
        if isinstance(row, dict):
            bead_ids.extend(str(v) for v in row.get("bead_ids") or [] if str(v))

    return {
        "ok": True,
        "source_system": "discord",
        "channel_id": channel_id,
        "guild_id": guild_id,
        "session_id": out.get("session_id"),
        "turns_ingested": out.get("turns_received"),
        "turns_paired": out.get("turns_paired"),
        "bead_ids": list(dict.fromkeys(bead_ids)),
        "raw": out,
    }
