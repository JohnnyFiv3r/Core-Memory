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


__all__ = ["soul_injection", "DEFAULT_INJECT_FILES"]
