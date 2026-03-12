"""
Write trigger execution layer.

DEPRECATED: This module previously used subprocess CLI execution.
Replaced with direct function calls for easier debugging and testing.
"""
from __future__ import annotations

import json
import os
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
    """Legacy trigger dispatcher.

    Disabled by default. When enabled, routes to canonical owners:
    - rolling_window_refresh -> run_rolling_window_pipeline
    - consolidate_session -> memory_engine.process_flush
    """
    event_id = str(event.get("event_id") or "")
    if event_id and _is_processed(root, event_id):
        return {"ok": True, "event_id": event_id, "skipped": True, "reason": "already_processed"}

    if str(os.getenv("CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        _mark_processed(root, event_id or "", "blocked", {"reason": "legacy_write_triggers_disabled"})
        return {
            "ok": False,
            "event_id": event_id,
            "error": "legacy_write_triggers_disabled",
            "authority_path": "canonical_in_process",
        }

    ttype = str(event.get("trigger_type") or "")
    payload = dict(event.get("payload") or {})

    if ttype == "rolling_window_refresh":
        from core_memory.write_pipeline.orchestrate import run_rolling_window_pipeline

        try:
            result = run_rolling_window_pipeline(
                token_budget=int(payload.get("token_budget") or 3000),
                max_beads=int(payload.get("max_beads") or 80),
            )
            _mark_processed(root, event_id or "", "done", {"trigger_type": ttype, "delegated_to": "run_rolling_window_pipeline"})
            return {"ok": True, "event_id": event_id, "result": result, "authority_path": "canonical_in_process"}
        except Exception as e:
            _mark_processed(root, event_id or "", "failed", {"error": str(e)[:500]})
            return {"ok": False, "event_id": event_id, "error": str(e), "authority_path": "canonical_in_process"}

    elif ttype == "consolidate_session":
        session = str(payload.get("session") or "")
        if not session:
            _mark_processed(root, event_id or "", "failed", {"error": "missing_session"})
            return {"ok": False, "error": "missing_session", "authority_path": "canonical_in_process"}

        from core_memory.memory_engine import process_flush

        try:
            result = process_flush(
                root=str(root),
                session_id=session,
                promote=bool(payload.get("promote", True)),
                token_budget=int(payload.get("token_budget") or 3000),
                max_beads=int(payload.get("max_beads") or 80),
                source="legacy_write_trigger",
                flush_tx_id=str(event_id or f"flush-{session}"),
            )
            _mark_processed(root, event_id or "", "done", {"trigger_type": ttype, "session": session, "delegated_to": "memory_engine.process_flush"})
            return {"ok": True, "event_id": event_id, "result": result, "authority_path": "canonical_in_process"}
        except Exception as e:
            _mark_processed(root, event_id or "", "failed", {"error": str(e)[:500]})
            return {"ok": False, "event_id": event_id, "error": str(e), "authority_path": "canonical_in_process"}

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
            "authority_path": "canonical_in_process",
        }
    else:
        _mark_processed(root, event_id or "", "ignored", {"reason": "unknown_trigger_type", "trigger_type": ttype})
        return {"ok": False, "error": "unknown_trigger_type", "trigger_type": ttype, "authority_path": "canonical_in_process"}
