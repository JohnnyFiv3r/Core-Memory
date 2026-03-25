from __future__ import annotations

import json
import uuid
import logging
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from datetime import datetime, timezone
from ..association.crawler_contract import build_crawler_context, merge_crawler_updates_for_flush, _crawler_updates_log_path
from .association_pass import run_association_pass
from ..write_pipeline.continuity_injection import load_continuity_injection
from .state import get_memory_pass, mark_memory_pass, try_claim_memory_pass
from .ingress import maybe_emit_finalize_memory_event
from .worker import SidecarPolicy, process_memory_event
from ..write_pipeline.orchestrate import run_consolidate_pipeline
from ..persistence.io_utils import append_jsonl
from ..persistence.store import MemoryStore
from .decision_pass import run_session_decision_pass
from ..policy.bead_typing import classify_bead_type

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
    # LLM-first policy classifier (with deterministic fallback) lives in policy layer.
    return classify_bead_type(user_query=user_query, assistant_final=assistant_final)


def _session_visible_bead_ids(root: str, session_id: str) -> list[str]:
    s = MemoryStore(root=root)
    idx = s._read_json(Path(root) / ".beads" / "index.json")
    out = []
    for bid, bead in (idx.get("beads") or {}).items():
        if str((bead or {}).get("session_id") or "") == str(session_id):
            out.append(str(bid))
    out.sort()
    return out


def _default_crawler_updates(req: dict[str, Any]) -> dict[str, Any]:
    user_query = str(req.get("user_query") or "").strip()
    assistant_final = str(req.get("assistant_final") or "").strip()
    title = (user_query or assistant_final or "Turn memory").splitlines()[0][:160]
    summary = (user_query or assistant_final or "turn memory")
    # Extract a "because" reason from the user query for promotion quality gate
    because = [user_query[:240]] if user_query else []
    return {
        "beads_create": [
            {
                "type": _infer_semantic_bead_type(user_query, assistant_final),
                "title": title or "Turn memory",
                "summary": [summary[:240]],
                "because": because,
                "source_turn_ids": [str(req.get("turn_id") or "")],
                "tags": ["crawler_reviewed", "turn_finalized"],
                "detail": assistant_final[:1200] if assistant_final else summary[:1200],
            }
        ]
    }


def _ensure_turn_creation_update(req: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Guarantee one current-turn creation candidate exists for canonical per-turn bead write."""
    out = dict(updates or {})
    key = "beads_create"
    rows = list(out.get(key) or [])
    turn_id = str(req.get("turn_id") or "")

    has_turn = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        src = [str(x) for x in (row.get("source_turn_ids") or []) if str(x)]
        if turn_id and turn_id in src:
            has_turn = True
            break

    if not has_turn:
        user_query = str(req.get("user_query") or "").strip()
        assistant_final = str(req.get("assistant_final") or "").strip()
        title = (user_query or assistant_final or "Turn memory").splitlines()[0][:160]
        summary = (user_query or assistant_final or "turn memory")
        because = [user_query[:240]] if user_query else []
        rows.append(
            {
                "type": _infer_semantic_bead_type(user_query, assistant_final),
                "title": title or "Turn memory",
                "summary": [summary[:240]],
                "because": because,
                "source_turn_ids": [turn_id],
                "tags": ["crawler_reviewed", "turn_finalized", "seeded_by_engine"],
                "detail": assistant_final[:1200] if assistant_final else summary[:1200],
            }
        )

    out[key] = rows
    return out


def _queue_preview_associations(root: str, session_id: str, visible_bead_ids: list[str]) -> int:
    """Promote association_preview candidates from newly created beads to the side log.

    Reads the index for session beads that have association_preview entries,
    and queues them as association_append entries so they commit at flush.
    """
    store = MemoryStore(root=root)
    idx = store._read_json(Path(root) / ".beads" / "index.json")
    beads = idx.get("beads") or {}
    visible = set(visible_bead_ids)
    log_path = _crawler_updates_log_path(root, session_id)
    now = datetime.now(timezone.utc).isoformat()

    # Collect existing association keys to avoid duplicates
    existing_keys: set[tuple[str, str]] = set()
    for a in (idx.get("associations") or []):
        src = str(a.get("source_bead") or "")
        tgt = str(a.get("target_bead") or "")
        if src and tgt:
            existing_keys.add((src, tgt))

    queued = 0
    for bid, bead in beads.items():
        if str(bead.get("session_id") or "") != session_id:
            continue
        previews = bead.get("association_preview") or []
        for preview in previews:
            target_id = str(preview.get("bead_id") or "")
            if not target_id or target_id not in beads:
                continue
            if (bid, target_id) in existing_keys or (target_id, bid) in existing_keys:
                continue
            rel = str(preview.get("relationship") or "related_to")
            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "association_append",
                    "session_id": session_id,
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "source_bead": bid,
                    "target_bead": target_id,
                    "relationship": rel,
                    "edge_class": "preview_promoted",
                    "confidence": preview.get("score", 0),
                    "created_at": now,
                },
            )
            existing_keys.add((bid, target_id))
            queued += 1

    return queued


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
    reviewed_updates = _ensure_turn_creation_update(req, reviewed_updates)

    crawler_visible = list(crawler_ctx.get("visible_bead_ids") or [])
    session_visible = _session_visible_bead_ids(root=root, session_id=req["session_id"])
    visible_ids = sorted(set(crawler_visible + session_visible))

    auto_apply = run_association_pass(
        root=root,
        session_id=req["session_id"],
        updates=reviewed_updates,
        visible_bead_ids=visible_ids,
    )

    # Recompute visible IDs after association pass created new beads.
    session_visible_after = _session_visible_bead_ids(root=root, session_id=req["session_id"])
    visible_ids = sorted(set(crawler_visible + session_visible_after))

    # Infer associations from store's association_preview candidates.
    # The store writes preview candidates when a bead is created; promote
    # them to queued associations so they commit at flush.
    _queue_preview_associations(root=root, session_id=req["session_id"], visible_bead_ids=visible_ids)

    # Canonical per-turn state decision pass for all visible session beads.
    decision_pass = run_session_decision_pass(
        root=root,
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
    out = run_association_pass(
        root=root,
        session_id=session_id,
        updates=updates,
        visible_bead_ids=visible_bead_ids,
    )
    out.setdefault("engine", {})
    out["engine"].update({"entry": "apply_crawler_turn_updates"})
    return out


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out
