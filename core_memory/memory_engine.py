from __future__ import annotations

import json
import os
import uuid
import logging
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from .association import build_crawler_context, apply_crawler_updates, merge_crawler_updates_for_flush
from .continuity_injection import load_continuity_injection
from .event_state import get_memory_pass, mark_memory_pass, try_claim_memory_pass
from .event_ingress import maybe_emit_finalize_memory_event
from .event_worker import SidecarPolicy, process_memory_event
from .write_pipeline.orchestrate import run_consolidate_pipeline
from .io_utils import append_jsonl
from .store import MemoryStore

logger = logging.getLogger(__name__)


# Canonical runtime center.
# P6A Step 2: deepen ownership by making memory_engine responsible for
# input normalization/defaulting and orchestration preflight composition.


def _normalize_turn_request(
    *,
    session_id: str,
    turn_id: str,
    transaction_id: str | None,
    trace_id: str | None,
    user_query: str,
    assistant_final: str,
    trace_depth: int,
    origin: str,
    tools_trace: list[dict] | None,
    mesh_trace: list[dict] | None,
    window_turn_ids: list[str] | None,
    window_bead_ids: list[str] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    tx = str(transaction_id or f"tx-{tid}-{uuid.uuid4().hex[:8]}")
    tr = str(trace_id or f"tr-{tid}-{uuid.uuid4().hex[:8]}")

    return {
        "session_id": sid,
        "turn_id": tid,
        "transaction_id": tx,
        "trace_id": tr,
        "user_query": str(user_query or ""),
        "assistant_final": str(assistant_final or ""),
        "trace_depth": int(trace_depth or 0),
        "origin": str(origin or "USER_TURN"),
        "tools_trace": list(tools_trace or []),
        "mesh_trace": list(mesh_trace or []),
        "window_turn_ids": [str(x) for x in (window_turn_ids or [])],
        "window_bead_ids": [str(x) for x in (window_bead_ids or [])],
        "metadata": dict(metadata or {}),
    }


def _infer_semantic_bead_type(user_query: str, assistant_final: str) -> str:
    text = f"{user_query} {assistant_final}".lower()
    if any(k in text for k in ["decide", "decision", "we chose", "chose", "policy"]):
        return "decision"
    if any(k in text for k in ["outcome", "result", "completed", "done", "shipped"]):
        return "outcome"
    if any(k in text for k in ["lesson", "learned", "insight"]):
        return "lesson"
    return "context"


def _default_crawler_updates(req: dict[str, Any]) -> dict[str, Any]:
    title = (req.get("assistant_final") or req.get("user_query") or "Turn memory").strip().splitlines()[0][:160]
    summary = (req.get("assistant_final") or req.get("user_query") or "").strip()
    if not summary:
        summary = "turn memory"
    return {
        "beads_create": [
            {
                "type": _infer_semantic_bead_type(str(req.get("user_query") or ""), str(req.get("assistant_final") or "")),
                "title": title or "Turn memory",
                "summary": [summary[:240]],
                "source_turn_ids": [str(req.get("turn_id") or "")],
                "tags": ["crawler_reviewed", "turn_finalized"],
                "detail": summary[:1200],
            }
        ]
    }


def process_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
    user_query: str,
    assistant_final: str,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    policy: SidecarPolicy | None = None,
) -> dict[str, Any]:
    req = _normalize_turn_request(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )

    emitted = maybe_emit_finalize_memory_event(
        root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        trace_depth=req["trace_depth"],
        origin=req["origin"],
        tools_trace=req["tools_trace"],
        mesh_trace=req["mesh_trace"],
        window_turn_ids=req["window_turn_ids"],
        window_bead_ids=req["window_bead_ids"],
        metadata=req["metadata"],
    )

    if not emitted.get("emitted"):
        return {
            "ok": True,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 0,
            "emitted": emitted,
            "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
        }

    row = emitted.get("payload")
    if not row:
        events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
        if events_file.exists():
            for line in events_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                env = r.get("envelope") or {}
                if env.get("session_id") == req["session_id"] and env.get("turn_id") == req["turn_id"]:
                    row = r
        if not row:
            return {
                "ok": False,
                "mode": "turn",
                "authority_path": "canonical_in_process",
                "processed": 0,
                "failed": 1,
                "error": "event_row_not_found",
                "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
            }

    claimed, state_after = try_claim_memory_pass(Path(root), req["session_id"], req["turn_id"])
    if not claimed:
        return {
            "ok": True,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 0,
            "reason": "not_claimed",
            "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
        }

    try:
        delta = process_memory_event(root, row, policy=policy)
    except Exception as exc:
        logger.warning("memory_engine.turn.process_memory_event_failed", exc_info=exc)
        mark_memory_pass(
            Path(root),
            req["session_id"],
            req["turn_id"],
            "failed",
            envelope_hash=(state_after or {}).get("envelope_hash", ""),
            reason="direct_turn_exception",
            error=str(exc),
        )
        return {
            "ok": False,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 1,
            "error": str(exc),
            "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
        }

    # V2P15 Step 2: enforce canonical crawler handoff framing from turn pipeline.
    crawler_ctx = build_crawler_context(root=root, session_id=req["session_id"], limit=200)
    auto_apply = None
    md = req.get("metadata") or {}
    reviewed_updates = md.get("crawler_updates") if isinstance(md, dict) else None
    if not isinstance(reviewed_updates, dict) or not reviewed_updates:
        reviewed_updates = _default_crawler_updates(req)

    visible_ids = list(crawler_ctx.get("visible_bead_ids") or [])
    auto_apply = apply_crawler_updates(
        root=root,
        session_id=req["session_id"],
        updates=reviewed_updates,
        visible_bead_ids=visible_ids,
    )

    # Canonical per-turn state decision pass for all visible session beads.
    decision_pass = MemoryStore(root=root).decide_session_promotion_states(
        session_id=req["session_id"],
        visible_bead_ids=visible_ids,
        turn_id=req["turn_id"],
    )

    return {
        "ok": True,
        "mode": "turn",
        "authority_path": "canonical_in_process",
        "processed": 1,
        "failed": 0,
        "delta": delta,
        "emitted": emitted,
        "crawler_handoff": {
            "required": True,
            "context_visible_count": len(visible_ids),
            "auto_apply": auto_apply,
            "decision_pass": decision_pass,
        },
        "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
    }


def _flush_state_file(root: str) -> Path:
    return Path(root) / ".beads" / "events" / "flush-state.json"


def _read_flush_state(root: str) -> dict[str, Any]:
    p = _flush_state_file(root)
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


def _write_flush_state(root: str, state: dict[str, Any]) -> None:
    p = _flush_state_file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def process_flush(
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

    # enrichment barrier check
    latest_turn = ""
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
            if ts >= latest_ts:
                latest_ts = ts
                latest_turn = str(env.get("turn_id") or "")

    if latest_turn:
        st = get_memory_pass(Path(root), session_id, latest_turn) or {}
        if str(st.get("status") or "") != "done":
            return {
                "ok": False,
                "authority_path": "canonical_in_process",
                "error": "enrichment_barrier_not_satisfied",
                "barrier": {"latest_turn_id": latest_turn, "latest_turn_status": str(st.get("status") or "unknown")},
                "engine": {
                    "entry": "process_flush",
                    "sequence_owner": "memory_engine",
                    "live_session_authority": str(live.get("authority") or "unknown"),
                    "live_session_count": int(live.get("count") or 0),
                },
            }

    checkpoints = Path(root) / ".beads" / "events" / "flush-checkpoints.jsonl"

    # Once-per-cycle/session guard: skip duplicate flush for same latest processed turn.
    state = _read_flush_state(root)
    sess_state = ((state.get("sessions") or {}).get(str(session_id)) or {}) if isinstance(state, dict) else {}
    if latest_turn and str(sess_state.get("last_flushed_turn_id") or "") == str(latest_turn):
        skipped_out = {
            "ok": True,
            "skipped": True,
            "reason": "already_flushed_for_latest_turn",
            "latest_turn_id": str(latest_turn),
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
                "result": flush_failed,
            },
        )
        return flush_failed

    flush_id_final = str(flush_tx_id or f"flush-{session_id}")
    append_jsonl(
        checkpoints,
        {
            "schema": "openclaw.memory.flush_checkpoint.v1",
            "stage": "committed",
            "session_id": str(session_id or ""),
            "source": str(source or "flush_hook"),
            "flush_tx_id": flush_id_final,
            "latest_turn_id": str(latest_turn or ""),
            "crawler_merge": merge_out,
        },
    )

    # Persist once-per-cycle state marker.
    state = _read_flush_state(root)
    sessions = state.setdefault("sessions", {}) if isinstance(state, dict) else {}
    if isinstance(sessions, dict):
        sessions[str(session_id)] = {
            "last_flushed_turn_id": str(latest_turn or ""),
            "last_flush_tx_id": flush_id_final,
            "last_flush_source": str(source or "flush_hook"),
        }
        _write_flush_state(root, state)

    flush_ok = {
        "ok": True,
        "authority_path": "canonical_in_process",
        "flush_tx_id": flush_id_final,
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
            "result": flush_ok,
        },
    )
    return flush_ok


def read_live_session(*, root: str, session_id: str) -> dict[str, Any]:
    return read_live_session_beads(root, session_id)


def emit_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
    user_query: str,
    assistant_final: str,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req = _normalize_turn_request(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )
    out = maybe_emit_finalize_memory_event(
        root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        trace_depth=req["trace_depth"],
        origin=req["origin"],
        tools_trace=req["tools_trace"],
        mesh_trace=req["mesh_trace"],
        window_turn_ids=req["window_turn_ids"],
        window_bead_ids=req["window_bead_ids"],
        metadata=req["metadata"],
    )
    out.setdefault("engine", {})
    out["engine"].update({"normalized": True, "entry": "emit_turn_finalized"})
    return out


def crawler_turn_context(*, root: str, session_id: str, limit: int = 200, carry_in_bead_ids: list[str] | None = None) -> dict[str, Any]:
    out = build_crawler_context(root=root, session_id=session_id, limit=limit, carry_in_bead_ids=carry_in_bead_ids)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "crawler_turn_context"})
    return out


def apply_crawler_turn_updates(
    *, root: str, session_id: str, updates: dict[str, Any], visible_bead_ids: list[str] | None = None
) -> dict[str, Any]:
    out = apply_crawler_updates(root=root, session_id=session_id, updates=updates, visible_bead_ids=visible_bead_ids)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "apply_crawler_turn_updates"})
    return out


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out


def process_pending_legacy_events(root: str, max_events: int = 50, policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Legacy compatibility poller, routed through memory_engine ownership."""
    legacy_enabled = os.environ.get("CORE_MEMORY_ENABLE_LEGACY_POLLER", "0") == "1"
    if not legacy_enabled:
        return {
            "processed": 0,
            "failed": 0,
            "skipped": True,
            "reason": "legacy_poller_disabled",
            "authority_path": "legacy_sidecar_compat",
            "engine": {"entry": "process_pending_legacy_events"},
        }

    events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
    if not events_file.exists():
        return {
            "processed": 0,
            "failed": 0,
            "authority_path": "legacy_sidecar_compat",
            "engine": {"entry": "process_pending_legacy_events"},
        }

    processed = 0
    failed = 0

    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            if processed >= max_events:
                break
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            envelope = row.get("envelope") or {}
            if not envelope:
                continue

            if (envelope.get("origin") or "").upper() == "MEMORY_PASS":
                continue

            session_id = envelope.get("session_id", "")
            turn_id = envelope.get("turn_id", "")
            prior = get_memory_pass(Path(root), session_id, turn_id)
            if prior and prior.get("status") == "done":
                continue

            claimed, state_after = try_claim_memory_pass(Path(root), session_id, turn_id)
            if not claimed:
                continue

            try:
                process_memory_event(root, row, policy=policy)
                processed += 1
            except Exception as exc:
                failed += 1
                mark_memory_pass(
                    Path(root),
                    session_id,
                    turn_id,
                    "failed",
                    envelope_hash=(state_after or {}).get("envelope_hash", ""),
                    reason="worker_exception",
                    error=str(exc),
                )

    return {
        "processed": processed,
        "failed": failed,
        "authority_path": "legacy_sidecar_compat",
        "engine": {"entry": "process_pending_legacy_events"},
    }
