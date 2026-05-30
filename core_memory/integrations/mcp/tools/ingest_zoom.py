"""MCP ingest adapter for Zoom/Otter meeting transcripts (#10B).

Accepts two formats:
- Zoom VTT (WebVTT) — the standard transcript export from Zoom recordings
- Otter.ai JSON — segment-based diarization export

``source_system`` is scoped to the recording ID (``"zoom:{recording_id}"`` or
``"otter:{recording_id}"``) so ``SPEAKER_00`` labels from different recordings
never falsely merge in the entity registry — a positional diarization label is
only meaningful within its recording.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.transcript_ingest import ingest_transcript

_VTT_TIMESTAMP_RE = re.compile(r"(\d{2,}):(\d{2}):(\d{2})[.,](\d{3})")
_VTT_SPEAKER_RE = re.compile(r"^([^:]{1,60}):\s*(.*)$")


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def _ts_to_iso(hours: str, minutes: str, seconds: str, ms: str) -> str:
    # VTT timecodes are recording-relative offsets — they cannot be converted to
    # absolute timestamps without knowing the recording start time. Return empty
    # so transcript_ingest skips timestamp validation for these turns.
    return ""


def _parse_vtt(text: str) -> list[dict[str, Any]]:
    """Parse Zoom WebVTT transcript into turn dicts."""
    out: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    current_ts = ""

    while i < len(lines):
        line = lines[i].strip()

        if not line or line == "WEBVTT" or line.isdigit():
            i += 1
            continue

        # Timestamp cue line: "00:00:01.000 --> 00:00:03.000"
        if "-->" in line:
            m = _VTT_TIMESTAMP_RE.search(line)
            if m:
                current_ts = _ts_to_iso(*m.groups())
            i += 1
            continue

        # Content line — may be "Speaker Name: content" or just content
        content_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            content_lines.append(lines[i].strip())
            i += 1

        full_text = " ".join(content_lines).strip()
        if not full_text:
            continue

        m = _VTT_SPEAKER_RE.match(full_text)
        if m:
            speaker, content = m.group(1).strip(), m.group(2).strip()
        else:
            speaker, content = "", full_text

        if content:
            out.append({
                "speaker": speaker or "SPEAKER",
                "role": "user",
                "content": content,
                "ts": current_ts or None,
            })

    return out


def _parse_otter(value: Any) -> list[dict[str, Any]]:
    """Parse Otter.ai JSON diarization export."""
    if not isinstance(value, dict):
        return []
    segments = value.get("transcript") or value.get("segments") or []
    if not isinstance(segments, list):
        return []
    out: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        content = str(seg.get("text") or seg.get("content") or "").strip()
        if not content:
            continue
        speaker = str(seg.get("speaker") or seg.get("speaker_label") or "SPEAKER").strip()
        # Otter start_time is a recording-relative offset in seconds — not an absolute
        # timestamp. Store it in metadata only; do not pass as ts.
        start = seg.get("start_time") or seg.get("start") or ""
        out.append({
            "speaker": speaker,
            "role": "user",
            "content": content,
            "ts": None,
            "metadata": {"vtt_offset_s": float(start)} if start else {},
        })
    return out


def _detect_format(path: Path | None, text: str, requested: str) -> str:
    if requested in {"vtt", "otter"}:
        return requested
    if path and path.suffix.lower() in {".vtt"}:
        return "vtt"
    stripped = text.lstrip()
    if stripped.startswith("WEBVTT"):
        return "vtt"
    return "otter"


def ingest_zoom_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ingest a Zoom or Otter.ai meeting transcript into Core Memory.

    Payload fields:
    - ``path``: path to a VTT or Otter JSON file (optional)
    - ``text``: inline VTT text (optional)
    - ``data``: inline Otter JSON dict (optional)
    - ``recording_id``: stable recording identifier — scopes SPEAKER_NN labels
      so they do not falsely merge across recordings (required for Zoom/Otter)
    - ``format``: "vtt" | "otter" | "auto" (default "auto")
    - ``source``: "zoom" | "otter" (default "zoom")
    - ``transcript_id``: override transcript identifier
    - ``session_id``: override session identifier
    - ``root``: Core Memory root path (default ".")
    - ``flush_policy``: "none" | "end_only" | "per_session"
    - ``window_size``: utterances per envelope (default 10)
    """
    payload = dict(payload or {})
    raw_path = str(payload.get("path") or "").strip()
    inline_text = payload.get("text")
    inline_data = payload.get("data")
    recording_id = str(payload.get("recording_id") or "").strip()
    source = str(payload.get("source") or "zoom").strip().lower()
    requested_fmt = str(payload.get("format") or "auto").strip().lower()

    turns: list[dict[str, Any]] = []
    fmt = "unknown"

    if inline_data is not None:
        fmt = "otter"
        turns = _parse_otter(inline_data)
    elif inline_text is not None:
        text = str(inline_text)
        fmt = _detect_format(None, text, requested_fmt)
        turns = _parse_vtt(text) if fmt == "vtt" else _parse_otter({})
    elif raw_path:
        p = Path(raw_path).expanduser()
        if not p.exists() or not p.is_file():
            return _error("cm.path_not_readable", "zoom ingest path is not a readable file", path=raw_path)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _error("cm.parser_aborted", str(exc), path=raw_path)
        fmt = _detect_format(p, text, requested_fmt)
        if fmt == "vtt":
            turns = _parse_vtt(text)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                return _error("cm.parser_aborted", f"invalid JSON: {exc}", path=raw_path)
            turns = _parse_otter(data)
    else:
        return _error("cm.path_not_readable", "ingest_zoom requires 'path', 'text', or 'data'")

    if not turns:
        return _error("cm.parser_aborted", "no usable segments found in meeting transcript")

    # Scope source_system to recording so SPEAKER_NN labels stay isolated
    if recording_id:
        source_system = f"{source}:{recording_id}"
    else:
        source_system = source

    transcript_id = str(
        payload.get("transcript_id") or recording_id or (Path(raw_path).stem if raw_path else f"{source}-transcript")
    )
    session_id = str(payload.get("session_id") or f"{source}:{transcript_id}")

    extra_meta: dict[str, Any] = {
        "source_system": source_system,
        "recording_id": recording_id,
        "transcript_format": fmt,
    }

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
        return _error("cm.ingest_failed", "meeting transcript ingest failed", errors=out.get("errors"))

    bead_ids: list[str] = []
    for row in out.get("ingested") or []:
        if isinstance(row, dict):
            bead_ids.extend(str(v) for v in row.get("bead_ids") or [] if str(v))

    return {
        "ok": True,
        "source_system": source_system,
        "recording_id": recording_id,
        "format": fmt,
        "session_id": out.get("session_id"),
        "turns_ingested": out.get("turns_received"),
        "turns_paired": out.get("turns_paired"),
        "bead_ids": list(dict.fromkeys(bead_ids)),
        "raw": out,
    }
