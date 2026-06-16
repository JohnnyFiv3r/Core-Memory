"""SOUL working-memory injection (PRD §4.3).

SOUL is injected into working memory at session start, not retrieved on demand —
the agent reasons *from* its self-model rather than rediscovering it. This builds
the read-only injection payload from the current SOUL projection; it is returned
fresh each session (never baked into the immutable session_start bead, since SOUL
evolves).

``SOUL.md`` is the primary surface; ``GOALS.md`` and ``TENSIONS.md`` are included
when they have content. Empty files are omitted so injection stays compact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.soul.store import DEFAULT_SUBJECT, read_soul_file

DEFAULT_INJECT_FILES = ("SOUL.md", "GOALS.md", "TENSIONS.md")
SOUL_INJECTION_HEADER = "# Self-Model (SOUL)"


def soul_injection(
    root: str | Path,
    *,
    subject: str = DEFAULT_SUBJECT,
    include: tuple[str, ...] = DEFAULT_INJECT_FILES,
) -> dict[str, Any]:
    """Return the SOUL surfaces to inject at session start for ``subject``.

    ``{ok, subject, present, files: {name: markdown}, injected_files}`` — only
    files with at least one entry are included.
    """
    files: dict[str, str] = {}
    for name in include:
        out = read_soul_file(root, file_name=name, subject=subject)
        if out.get("ok") and int(out.get("entry_count") or 0) > 0:
            files[str(out.get("file_name") or name)] = str(out.get("markdown") or "")
    return {
        "ok": True,
        "subject": str(subject or DEFAULT_SUBJECT),
        "present": bool(files),
        "files": files,
        "injected_files": sorted(files.keys()),
    }


def soul_injection_text(root: str | Path, *, subject: str = DEFAULT_SUBJECT) -> str:
    """Render the SOUL injection as a ready-to-prepend prompt block, or "" when
    the self-model is empty. Adapters concatenate this into their session-start
    context so the agent actually sees its self-model (not just the host)."""
    out = soul_injection(root, subject=subject)
    if not out.get("present"):
        return ""
    parts = [SOUL_INJECTION_HEADER]
    for name in (out.get("injected_files") or []):
        block = str((out.get("files") or {}).get(name) or "").strip()
        if block:
            parts.append(block)
    return "\n\n".join(parts).strip()


__all__ = ["soul_injection", "soul_injection_text", "DEFAULT_INJECT_FILES", "SOUL_INJECTION_HEADER"]
