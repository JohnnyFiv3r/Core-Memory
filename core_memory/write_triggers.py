"""
Write trigger execution layer.

DEPRECATED: This module previously used subprocess CLI execution.
Replaced with direct function calls for easier debugging and testing.
"""
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


def _processed_file(root: str | Path) -> Path:
    rp = Path(root)
    p = rp / ".beads" / "events" / "write-trigger-processed.jsonl"
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

    Additive and non-disruptive: records trigger authority intent.
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


def _is_processed(root: str | Path, event_id: str) -> bool:
    p = _processed_file(root)
    if not p.exists():
        return False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if str(rec.get("event_id") or "") == str(event_id):
            return True
    return False


def _mark_processed(root: str | Path, event_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
    rec = {
        "event_id": str(event_id),
        "status": str(status),
        "detail": detail or {},
        "processed_at": _now_iso(),
    }
    p = _processed_file(root)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def dispatch_write_trigger(root: str | Path, event: dict[str, Any], workspace_root: str | Path = ".") -> dict[str, Any]:
    """Dispatch a write trigger via direct function calls.

    Replaced subprocess execution with direct imports for:
    - Easier debugging
    - Fewer failure modes
    - Better unit testing
    """
    event_id = str(event.get("event_id") or "")
    if event_id and _is_processed(root, event_id):
        return {"ok": True, "event_id": event_id, "skipped": True, "reason": "already_processed"}

    ttype = str(event.get("trigger_type") or "")
    payload = dict(event.get("payload") or {})

    if ttype == "rolling_window_refresh":
        # Direct call instead of subprocess
        from core_memory.write_pipeline.consolidate import consolidate_rolling_window
        try:
            result = consolidate_rolling_window(
                root=str(root),
                token_budget=int(payload.get("token_budget") or 3000),
                max_beads=int(payload.get("max_beads") or 80),
            )
            _mark_processed(root, event_id or "", "done", {"trigger_type": ttype})
            return {"ok": True, "event_id": event_id, "result": result}
        except Exception as e:
            _mark_processed(root, event_id or "", "failed", {"error": str(e)[:500]})
            return {"ok": False, "event_id": event_id, "error": str(e)}

    elif ttype == "consolidate_session":
        session = str(payload.get("session") or "")
        if not session:
            _mark_processed(root, event_id or "", "failed", {"error": "missing_session"})
            return {"ok": False, "error": "missing_session"}

        from core_memory.write_pipeline.consolidate import consolidate_session
        try:
            result = consolidate_session(
                root=str(root),
                session_id=session,
                token_budget=int(payload.get("token_budget") or 3000),
                max_beads=int(payload.get("max_beads") or 80),
                promote=bool(payload.get("promote")),
            )
            _mark_processed(root, event_id or "", "done", {"trigger_type": ttype, "session": session})
            return {"ok": True, "event_id": event_id, "result": result}
        except Exception as e:
            _mark_processed(root, event_id or "", "failed", {"error": str(e)[:500]})
            return {"ok": False, "event_id": event_id, "error": str(e)}

    elif ttype == "extract_beads":
        _mark_processed(
            root,
            event_id or "",
            "retired",
            {"reason": "extract_path_retired", "trigger_type": ttype},
        )
        return {
            "ok": False,
            "event_id": event_id,
            "error": "extract_path_retired",
            "trigger_type": ttype,
        }
    else:
        _mark_processed(root, event_id or "", "ignored", {"reason": "unknown_trigger_type", "trigger_type": ttype})
        return {"ok": False, "error": "unknown_trigger_type", "trigger_type": ttype}
