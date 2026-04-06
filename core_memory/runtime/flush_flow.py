from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from ..association.crawler_contract import merge_crawler_updates_for_flush
from .state import get_memory_pass
from ..write_pipeline.orchestrate import run_consolidate_pipeline
from ..persistence.io_utils import append_jsonl
from ..retrieval.lifecycle import mark_flush_checkpoint
from .flush_state import (
    read_flush_state,
    write_flush_state,
    upsert_process_flush_checkpoint_bead,
)


def process_flush_impl(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
) -> dict[str, Any]:
    live = read_live_session_beads(root, session_id)

    # enrichment barrier check (flush anchored to latest DONE turn)
    latest_turn = ""
    latest_turn_status = "unknown"
    latest_done_turn = ""
    session_turns: list[tuple[int, str]] = []
    events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
    if events_file.exists():
        latest_ts = -1
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            env = row.get("envelope") or {}
            if str(env.get("session_id") or "") != str(session_id):
                continue
            ts = int(env.get("ts_ms") or 0)
            turn_id = str(env.get("turn_id") or "")
            if not turn_id:
                continue
            session_turns.append((ts, turn_id))
            if ts >= latest_ts:
                latest_ts = ts
                latest_turn = turn_id

    if latest_turn:
        st = get_memory_pass(Path(root), session_id, latest_turn) or {}
        latest_turn_status = str(st.get("status") or "unknown")

    for _ts, tid in sorted(session_turns, key=lambda x: x[0], reverse=True):
        st = get_memory_pass(Path(root), session_id, tid) or {}
        if str(st.get("status") or "") == "done":
            latest_done_turn = tid
            break

    if latest_turn and not latest_done_turn:
        return {
            "ok": False,
            "retryable": True,
            "retry_after_seconds": 2,
            "authority_path": "canonical_in_process",
            "error": "enrichment_barrier_not_satisfied",
            "barrier": {
                "latest_turn_id": latest_turn,
                "latest_turn_status": latest_turn_status,
                "latest_done_turn_id": "",
            },
            "engine": {
                "entry": "process_flush",
                "sequence_owner": "memory_engine",
                "live_session_authority": str(live.get("authority") or "unknown"),
                "live_session_count": int(live.get("count") or 0),
            },
        }

    flush_anchor_turn = str(latest_done_turn or latest_turn or "")

    checkpoints = Path(root) / ".beads" / "events" / "flush-checkpoints.jsonl"

    # Once-per-cycle/session guard: skip duplicate flush for same latest processed turn.
    state = read_flush_state(root)
    sess_state = ((state.get("sessions") or {}).get(str(session_id)) or {}) if isinstance(state, dict) else {}
    if flush_anchor_turn and str(sess_state.get("last_flushed_turn_id") or "") == str(flush_anchor_turn):
        skipped_out = {
            "ok": True,
            "skipped": True,
            "reason": "already_flushed_for_latest_done_turn",
            "latest_turn_id": str(latest_turn or ""),
            "latest_done_turn_id": str(flush_anchor_turn),
            "latest_turn_status": str(latest_turn_status or "unknown"),
            "last_flush_tx_id": str(sess_state.get("last_flush_tx_id") or ""),
            "authority_path": "canonical_in_process",
            "engine": {
                "entry": "process_flush",
                "sequence_owner": "memory_engine",
                "live_session_authority": str(live.get("authority") or "unknown"),
                "live_session_count": int(live.get("count") or 0),
            },
        }
        append_jsonl(
            checkpoints,
            {
                "schema": "openclaw.memory.flush_report.v1",
                "stage": "skipped",
                "session_id": str(session_id or ""),
                "source": str(source or "flush_hook"),
                "flush_tx_id": str(sess_state.get("last_flush_tx_id") or f"flush-{session_id}"),
                "latest_turn_id": str(latest_turn or ""),
                "latest_done_turn_id": str(flush_anchor_turn or ""),
                "latest_turn_status": str(latest_turn_status or "unknown"),
                "result": skipped_out,
            },
        )
        return skipped_out

    append_jsonl(
        checkpoints,
        {
            "schema": "openclaw.memory.flush_checkpoint.v1",
            "stage": "start",
            "session_id": str(session_id or ""),
            "source": str(source or "flush_hook"),
            "flush_tx_id": str(flush_tx_id or f"flush-{session_id}"),
        },
    )

    merge_out = merge_crawler_updates_for_flush(root=root, session_id=str(session_id or ""))

    out = run_consolidate_pipeline(
        session_id=str(session_id or ""),
        promote=bool(promote),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        root=root,
        workspace_root=root,
    )
    if not out.get("ok"):
        flush_failed = {
            "ok": False,
            "authority_path": "canonical_in_process",
            "error": out.get("error") or "flush_failed",
            "result": out,
            "crawler_merge": merge_out,
            "engine": {
                "entry": "process_flush",
                "sequence_owner": "memory_engine",
                "live_session_authority": str(live.get("authority") or "unknown"),
                "live_session_count": int(live.get("count") or 0),
            },
        }
        append_jsonl(
            checkpoints,
            {
                "schema": "openclaw.memory.flush_checkpoint.v1",
                "stage": "failed",
                "session_id": str(session_id or ""),
                "source": str(source or "flush_hook"),
                "flush_tx_id": str(flush_tx_id or f"flush-{session_id}"),
                "error": out.get("error") or "flush_failed",
            },
        )
        append_jsonl(
            checkpoints,
            {
                "schema": "openclaw.memory.flush_report.v1",
                "stage": "failed",
                "session_id": str(session_id or ""),
                "source": str(source or "flush_hook"),
                "flush_tx_id": str(flush_tx_id or f"flush-{session_id}"),
                "latest_turn_id": str(latest_turn or ""),
                "latest_done_turn_id": str(flush_anchor_turn or ""),
                "latest_turn_status": str(latest_turn_status or "unknown"),
                "result": flush_failed,
            },
        )
        return flush_failed

    flush_id_final = str(flush_tx_id or f"flush-{session_id}")
    checkpoint_bead_id, checkpoint_created = upsert_process_flush_checkpoint_bead(
        root=root,
        session_id=str(session_id or ""),
        flush_tx_id=flush_id_final,
        latest_turn_id=str(latest_turn or ""),
        latest_done_turn_id=str(flush_anchor_turn or ""),
        latest_turn_status=str(latest_turn_status or "unknown"),
        source=str(source or "flush_hook"),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        promote=bool(promote),
    )
    append_jsonl(
        checkpoints,
        {
            "schema": "openclaw.memory.flush_checkpoint.v1",
            "stage": "committed",
            "session_id": str(session_id or ""),
            "source": str(source or "flush_hook"),
            "flush_tx_id": flush_id_final,
            "latest_turn_id": str(latest_turn or ""),
            "latest_done_turn_id": str(flush_anchor_turn or ""),
            "latest_turn_status": str(latest_turn_status or "unknown"),
            "checkpoint_bead_id": str(checkpoint_bead_id or ""),
            "checkpoint_bead_created": bool(checkpoint_created),
            "crawler_merge": merge_out,
        },
    )

    # Persist once-per-cycle state marker.
    state = read_flush_state(root)
    sessions = state.setdefault("sessions", {}) if isinstance(state, dict) else {}
    if isinstance(sessions, dict):
        sessions[str(session_id)] = {
            "last_flushed_turn_id": str(flush_anchor_turn or ""),
            "last_flush_tx_id": flush_id_final,
            "last_flush_source": str(source or "flush_hook"),
            "last_seen_turn_id": str(latest_turn or ""),
            "last_seen_turn_status": str(latest_turn_status or "unknown"),
        }
        write_flush_state(root, state)

    mark_flush_checkpoint(root, flush_tx_id=flush_id_final)

    flush_ok = {
        "ok": True,
        "authority_path": "canonical_in_process",
        "flush_tx_id": flush_id_final,
        "latest_turn_id": str(latest_turn or ""),
        "latest_done_turn_id": str(flush_anchor_turn or ""),
        "latest_turn_status": str(latest_turn_status or "unknown"),
        "checkpoint_bead_id": str(checkpoint_bead_id or ""),
        "checkpoint_bead_created": bool(checkpoint_created),
        "crawler_merge": merge_out,
        "result": out,
        "engine": {
            "entry": "process_flush",
            "sequence_owner": "memory_engine",
            "live_session_authority": str(live.get("authority") or "unknown"),
            "live_session_count": int(live.get("count") or 0),
        },
    }
    append_jsonl(
        checkpoints,
        {
            "schema": "openclaw.memory.flush_report.v1",
            "stage": "committed",
            "session_id": str(session_id or ""),
            "source": str(source or "flush_hook"),
            "flush_tx_id": flush_id_final,
            "latest_turn_id": str(latest_turn or ""),
            "latest_done_turn_id": str(flush_anchor_turn or ""),
            "latest_turn_status": str(latest_turn_status or "unknown"),
            "result": flush_ok,
        },
    )
    return flush_ok
