from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def session_file_path(root: str | Path, session_id: str) -> Path:
    return Path(root) / ".beads" / f"session-{session_id}.jsonl"


def read_session_surface(root: str | Path, session_id: str) -> list[dict[str, Any]]:
    """Read append-only session bead file as the live session surface.

    Groundwork step for V2-P3 session-authority cutover. Non-breaking: this is
    additive and can be used by orchestrators while index-first fallback paths
    remain during transition.
    """
    p = session_file_path(root, session_id)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows
