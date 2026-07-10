from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.association.crawler_contract import merge_crawler_updates_for_flush
from core_memory.persistence.io_utils import append_jsonl
from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id
from core_memory.retrieval.lifecycle import mark_flush_checkpoint
from core_memory.runtime.associations.coverage import enqueue_association_coverage
from core_memory.runtime.flush.flush_state import (
    read_flush_state,
    upsert_process_flush_checkpoint_bead,
    write_flush_state,
)
from core_memory.runtime.queue.side_effects import enqueue_post_write_side_effects
from core_memory.runtime.session.live_session import read_live_session_beads
from core_memory.runtime.turn.semantic_state import (
    get_semantic_flush_waiver,
    get_semantic_write_state,
    latest_finalized_turn,
    mark_semantic_write_state,
    record_semantic_flush_waiver,
)
from core_memory.schema.event_schemas import FLUSH_CHECKPOINT, FLUSH_REPORT
from core_memory.write_pipeline.orchestrate import run_consolidate_pipeline


def process_flush_impl(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
    semantic_override: bool = False,
    override_operator: str = "",
    override_reason: str = "",
) -> dict[str, Any]:
    live = read_live_session_beads(root, session_id)

    # Semantic barrier: only the latest finalized turn participates. Mechanical
    # memory-pass completion is not evidence that a canonical semantic bead was
    # written. Older pending turns remain visible through semantic-write health.
    latest_event = latest_finalized_turn(root, session_id) or {}
    latest_turn = str(latest_event.get("turn_id") or "")
    semantic_state = get_semantic_write_state(root, session_id, latest_turn) if latest_turn else None
    latest_turn_status = str((semantic_state or {}).get("status") or "unknown")
    canonical_bead_id = ""
    waiver: dict[str, Any] | None = None
    if latest_turn:
        canonical_bead_id = str(
            find_canonical_turn_bead_id(
                root,
                session_id=session_id,
                turn_id=latest_turn,
                preferred_bead_ids=[],
            )
            or ""
        )
        waiver = get_semantic_flush_waiver(root, session_id, latest_turn)
        if canonical_bead_id:
            prior_semantic_status = str((semantic_state or {}).get("status") or "")
            latest_turn_status = "repair_required" if prior_semantic_status == "repair_required" else "committed"
            if prior_semantic_status not in {"committed", "repair_required"}:
                mark_semantic_write_state(
                    root,
                    session_id=session_id,
                    turn_id=latest_turn,
                    status="committed",
                    event_id=str(latest_event.get("event_id") or ""),
                    bead_id=canonical_bead_id,
                    retryable=False,
                    reason="flush_barrier_reconciled_canonical_bead",
                )
        elif not waiver and semantic_override:
            if not str(override_operator or "").strip() or not str(override_reason or "").strip():
                return {
                    "ok": False,
                    "retryable": False,
                    "authority_path": "canonical_in_process",
                    "error": "invalid_semantic_flush_override",
                    "barrier": {
                        "latest_turn_id": latest_turn,
                        "latest_turn_status": latest_turn_status,
                        "semantic_status": latest_turn_status,
                        "canonical_bead_id": "",
                        "waiver_id": "",
                    },
                }
            waiver = record_semantic_flush_waiver(
                root,
                session_id=session_id,
                turn_id=latest_turn,
                event_id=str(latest_event.get("event_id") or ""),
                operator=override_operator,
                reason=override_reason,
            )
            latest_turn_status = "waived"
        elif waiver:
            latest_turn_status = "waived"

    latest_done_turn = latest_turn if canonical_bead_id or waiver else ""
    if latest_turn and not latest_done_turn:
        return {
            "ok": False,
            "retryable": True,
            "retry_after_seconds": 2,
            "authority_path": "canonical_in_process",
            "error": "semantic_write_barrier_not_satisfied",
            "barrier": {
                "latest_turn_id": latest_turn,
                "latest_turn_status": latest_turn_status,
                "latest_done_turn_id": "",
                "semantic_status": latest_turn_status,
                "canonical_bead_id": "",
                "waiver_id": "",
            },
            "engine": {
                "entry": "process_flush",
                "sequence_owner": "memory_engine",
                "live_session_authority": str(live.get("authority") or "unknown"),
                "live_session_count": int(live.get("count") or 0),
            },
        }

    flush_anchor_turn = str(latest_done_turn or latest_turn or "")
    semantic_barrier = {
        "latest_turn_id": latest_turn,
        "semantic_status": latest_turn_status,
        "canonical_bead_id": canonical_bead_id,
        "waiver_id": str((waiver or {}).get("waiver_id") or ""),
    }

    checkpoints = Path(root) / ".beads" / "events" / "flush-checkpoints.jsonl"

    # Once-per-cycle/session guard: skip duplicate flush for same latest processed turn.
    state = read_flush_state(root)
    sess_state = ((state.get("sessions") or {}).get(str(session_id)) or {}) if isinstance(state, dict) else {}
    if flush_anchor_turn and str(sess_state.get("last_flushed_turn_id") or "") == str(flush_anchor_turn):
        # Even when consolidation is skipped, recall traffic since the last
        # flush has accumulated edge-usage signal — fold it so reinforcement
        # keeps up with read activity on otherwise idle sessions. The fold is
        # store-locked and idempotent.
        try:
            from core_memory.association.edge_lifecycle import fold_edge_usage

            edge_lifecycle_out = fold_edge_usage(root)
        except Exception:
            edge_lifecycle_out = {"ok": False, "error": "edge_lifecycle_fold_failed"}
        try:
            association_coverage = enqueue_association_coverage(
                root=root,
                session_id=str(session_id or ""),
                trigger="session_flush",
                run_inline=False,
            )
        except Exception as exc:
            association_coverage = {"ok": False, "error": str(exc)}
        skipped_out = {
            "ok": True,
            "skipped": True,
            "edge_lifecycle": edge_lifecycle_out,
            "association_coverage": association_coverage,
            "reason": "already_flushed_for_latest_done_turn",
            "latest_turn_id": str(latest_turn or ""),
            "latest_done_turn_id": str(flush_anchor_turn),
            "latest_turn_status": str(latest_turn_status or "unknown"),
            "semantic_barrier": semantic_barrier,
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
                "schema": FLUSH_REPORT,
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
            "schema": FLUSH_CHECKPOINT,
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
            "semantic_barrier": semantic_barrier,
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
                "schema": FLUSH_CHECKPOINT,
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
                "schema": FLUSH_REPORT,
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
            "schema": FLUSH_CHECKPOINT,
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

    side_effects = enqueue_post_write_side_effects(
        root=root,
        session_id=str(session_id or ""),
        flush_tx_id=flush_id_final,
        source=str(source or "flush_hook"),
    )
    try:
        association_coverage = enqueue_association_coverage(
            root=root,
            session_id=str(session_id or ""),
            trigger="session_flush",
            run_inline=False,
        )
    except Exception as exc:
        association_coverage = {"ok": False, "error": str(exc)}

    # Edge lifecycle: fold recall-time edge-usage events into association
    # reinforcement fields. Session flush is the maintenance boundary — the
    # read path only logs, the write side applies.
    try:
        from core_memory.association.edge_lifecycle import fold_edge_usage

        edge_lifecycle_out = fold_edge_usage(root)
    except Exception:
        edge_lifecycle_out = {"ok": False, "error": "edge_lifecycle_fold_failed"}

    flush_ok = {
        "ok": True,
        "authority_path": "canonical_in_process",
        "edge_lifecycle": edge_lifecycle_out,
        "flush_tx_id": flush_id_final,
        "latest_turn_id": str(latest_turn or ""),
        "latest_done_turn_id": str(flush_anchor_turn or ""),
        "latest_turn_status": str(latest_turn_status or "unknown"),
        "semantic_barrier": semantic_barrier,
        "checkpoint_bead_id": str(checkpoint_bead_id or ""),
        "checkpoint_bead_created": bool(checkpoint_created),
        "crawler_merge": merge_out,
        "association_coverage": association_coverage,
        "post_write_side_effects": side_effects,
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
            "schema": FLUSH_REPORT,
            "stage": "committed",
            "session_id": str(session_id or ""),
            "source": str(source or "flush_hook"),
            "flush_tx_id": flush_id_final,
            "latest_turn_id": str(latest_turn or ""),
            "latest_done_turn_id": str(flush_anchor_turn or ""),
            "latest_turn_status": str(latest_turn_status or "unknown"),
            "post_write_side_effects": side_effects,
            "result": flush_ok,
        },
    )
    return flush_ok
