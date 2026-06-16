"""SOUL working-memory injection (PRD §4.3).

SOUL is injected into working memory at session start, not retrieved on demand —
the agent reasons *from* its self-model rather than rediscovering it. This builds
the read-only injection payload from the current SOUL projection; it is returned
fresh each session (never baked into the immutable session_start bead, since SOUL
evolves).

``SOUL.md`` is the primary surface; ``GOALS.md``, ``TENSIONS.md``, and
``IDENTITY.md`` are included when they have content. Empty files are omitted so
injection stays compact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.soul.store import DEFAULT_SUBJECT, current_soul_entries, read_soul_file

DEFAULT_INJECT_FILES = ("SOUL.md", "GOALS.md", "TENSIONS.md", "IDENTITY.md")
SOUL_INJECTION_HEADER = "# Self-Model (SOUL)"
EPISTEMIC_GROUPS = ("endorsed", "observed", "inferred")
_EPISTEMIC_LABELS = {
    "endorsed": "Endorsed",
    "observed": "Observed",
    "inferred": "Inferred",
}


def soul_injection(
    root: str | Path,
    *,
    subject: str = DEFAULT_SUBJECT,
    include: tuple[str, ...] = DEFAULT_INJECT_FILES,
) -> dict[str, Any]:
    """Return the SOUL surfaces to inject at session start for ``subject``.

    ``{ok, subject, present, files: {name: markdown}, injected_files,
    epistemic_groups}`` — only files with at least one entry are included.
    """
    files: dict[str, str] = {}
    groups: dict[str, list[dict[str, str]]] = {k: [] for k in EPISTEMIC_GROUPS}
    for name in include:
        out = read_soul_file(root, file_name=name, subject=subject)
        if out.get("ok") and int(out.get("entry_count") or 0) > 0:
            file_name = str(out.get("file_name") or name)
            files[file_name] = str(out.get("markdown") or "")
            entries_out = current_soul_entries(root, file_name=file_name, subject=subject)
            entries = (entries_out.get("entries") or {}) if entries_out.get("ok") else {}
            if isinstance(entries, dict):
                for entry_key, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    status = str(entry.get("epistemic_status") or "inferred").strip().lower()
                    if status not in groups:
                        status = "inferred"
                    groups[status].append({
                        "file_name": file_name,
                        "entry_key": str(entry_key or ""),
                        "content": str(entry.get("content") or ""),
                        "source": str(entry.get("source") or ""),
                        "revision_id": str(entry.get("revision_id") or ""),
                    })
    return {
        "ok": True,
        "subject": str(subject or DEFAULT_SUBJECT),
        "present": bool(files),
        "files": files,
        "injected_files": sorted(files.keys()),
        "epistemic_groups": {k: v for k, v in groups.items() if v},
    }


def soul_injection_text(root: str | Path, *, subject: str = DEFAULT_SUBJECT) -> str:
    """Render the SOUL injection as a ready-to-prepend prompt block, or "" when
    the self-model is empty. Adapters concatenate this into their session-start
    context so the agent actually sees its self-model (not just the host)."""
    out = soul_injection(root, subject=subject)
    if not out.get("present"):
        return ""
    parts = [SOUL_INJECTION_HEADER]
    groups = out.get("epistemic_groups") or {}
    for status in EPISTEMIC_GROUPS:
        rows = groups.get(status) or []
        if not rows:
            continue
        parts.append(f"## {_EPISTEMIC_LABELS[status]}")
        for row in rows:
            file_name = str(row.get("file_name") or "").strip()
            entry_key = str(row.get("entry_key") or "").strip()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            title = " / ".join([x for x in (file_name, entry_key) if x])
            parts.append(f"### {title}\n{content}" if title else content)
    return "\n\n".join(parts).strip()


__all__ = [
    "soul_injection",
    "soul_injection_text",
    "DEFAULT_INJECT_FILES",
    "SOUL_INJECTION_HEADER",
    "EPISTEMIC_GROUPS",
]
