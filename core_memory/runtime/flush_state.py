from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..persistence.store import MemoryStore


def flush_state_file(root: str) -> Path:
    return Path(root) / ".beads" / "events" / "flush-state.json"


def read_flush_state(root: str) -> dict[str, Any]:
    p = flush_state_file(root)
    if not p.exists():
        return {"sessions": {}}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            obj.setdefault("sessions", {})
            return obj
    except Exception:
        pass
    return {"sessions": {}}


def write_flush_state(root: str, state: dict[str, Any]) -> None:
    p = flush_state_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_process_flush_checkpoint_bead(
    *,
    root: str,
    session_id: str,
    flush_tx_id: str,
    latest_turn_id: str,
    latest_done_turn_id: str,
    latest_turn_status: str,
    source: str,
    token_budget: int,
    max_beads: int,
    promote: bool,
) -> tuple[str, bool]:
    """Create idempotent process_flush checkpoint bead; returns (bead_id, created_now)."""
    store = MemoryStore(root)
    idx = store._read_json(store.beads_dir / "index.json")
    for b in (idx.get("beads") or {}).values():
        if str(b.get("type") or "") != "process_flush":
            continue
        if str(b.get("flush_tx_id") or "") == str(flush_tx_id):
            return str(b.get("id") or ""), False

    title = f"process_flush checkpoint ({session_id})"
    summary = [
        f"flush_tx_id={flush_tx_id}",
        f"latest_turn_id={latest_turn_id or '-'}",
        f"latest_done_turn_id={latest_done_turn_id or '-'}",
        f"latest_turn_status={latest_turn_status or 'unknown'}",
    ]
    detail = (
        "Causal checkpoint written at process_flush commit boundary. "
        f"Source={source}; token_budget={int(token_budget)}; max_beads={int(max_beads)}; promote={bool(promote)}."
    )
    bead_id = store.add_bead(
        type="process_flush",
        title=title,
        summary=summary,
        detail=detail,
        session_id=str(session_id or ""),
        scope="project",
        tags=["checkpoint", "process_flush", "system_checkpoint"],
        source_turn_ids=[str(latest_done_turn_id or latest_turn_id or "")],
        authority="system",
        status="open",
        retrieval_exclude_default=True,
        checkpoint_scope="window",
        flush_tx_id=str(flush_tx_id),
        latest_turn_id=str(latest_turn_id or ""),
        latest_done_turn_id=str(latest_done_turn_id or ""),
        latest_turn_status=str(latest_turn_status or "unknown"),
        flush_source=str(source or "flush_hook"),
        flush_token_budget=int(token_budget),
        flush_max_beads=int(max_beads),
        flush_promote=bool(promote),
    )
    return str(bead_id), True
