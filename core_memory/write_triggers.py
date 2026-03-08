from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events_file(root: str | Path) -> Path:
    rp = Path(root)
    p = rp / ".beads" / "events" / "write-triggers.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def emit_write_trigger(
    *,
    root: str | Path,
    trigger_type: str,
    source: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Emit a canonical write-side trigger event.

    This is additive and non-disruptive: it records trigger authority intent
    without changing downstream write behavior.
    """
    event_id = f"wtr-{uuid.uuid4().hex[:16]}"
    rec = {
        "event_id": event_id,
        "kind": "write_trigger",
        "trigger_type": str(trigger_type or "unknown"),
        "source": str(source or "unknown"),
        "payload": payload or {},
        "created_at": _now_iso(),
    }
    f = _events_file(root)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return event_id
