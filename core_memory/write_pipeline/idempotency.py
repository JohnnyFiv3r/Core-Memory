from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def marker_path(root: str, session_id: str) -> Path:
    p = Path(root) / ".beads" / ".extracted" / f"session-{session_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def already_extracted(root: str, session_id: str) -> bool:
    return marker_path(root, session_id).exists()


def write_extracted_marker(root: str, session_id: str, transcript: str, written: int) -> None:
    rec = {
        "session_id": session_id,
        "transcript": transcript,
        "written": int(written),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    p = marker_path(root, session_id)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
