from __future__ import annotations

import json
import uuid
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from datetime import datetime, timezone
from core_memory.integrations.openclaw_flags import (
    agent_authored_fail_open_enabled,
    agent_authored_required_enabled,
)
from ..association.crawler_contract import (
    build_crawler_context,
    merge_crawler_updates,
    merge_crawler_updates_for_flush,
    _crawler_updates_log_path,
)
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
from ..policy.hygiene import enforce_bead_hygiene_contract, is_runtime_meta_chatter
from ..retrieval.lifecycle import mark_turn_checkpoint, mark_flush_checkpoint
from .agent_authored_contract import (
    ERROR_AGENT_UPDATES_INVALID,
    ERROR_AGENT_UPDATES_MISSING,
    validate_agent_authored_updates,
)

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


def _resolve_reviewed_updates(req: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    md = req.get("metadata") or {}
    reviewed = md.get("crawler_updates") if isinstance(md, dict) else None
    required = agent_authored_required_enabled()
    fail_open = agent_authored_fail_open_enabled()

    gate = {
        "required": bool(required),
        "fail_open": bool(fail_open),
        "source": "metadata.crawler_updates" if isinstance(reviewed, dict) and reviewed else "default_fallback",
        "used_fallback": False,
        "blocked": False,
        "error_code": None,
    }

    if isinstance(reviewed, dict) and reviewed:
        if required:
            ok, code, details = validate_agent_authored_updates(reviewed)
            gate["validation"] = details
            if not ok:
                gate["error_code"] = code
                if fail_open:
                    gate["source"] = "default_fallback"
                    gate["used_fallback"] = True
                    return _default_crawler_updates(req), gate
                gate["blocked"] = True
                return None, gate
        return dict(reviewed), gate

    if required:
        gate["error_code"] = ERROR_AGENT_UPDATES_INVALID if (isinstance(md, dict) and "crawler_updates" in md) else ERROR_AGENT_UPDATES_MISSING
        if not fail_open:
            gate["blocked"] = True
            return None, gate

    gate["source"] = "default_fallback"
    gate["used_fallback"] = True
    return _default_crawler_updates(req), gate


def _enforce_turn_row_invariants(root: str, req: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Enforce per-turn bead row invariants: source_turn_ids, required fields."""
    out = dict(row)
    turn_id = str(req.get("turn_id") or "")
    if turn_id:
        src = [str(x) for x in (out.get("source_turn_ids") or []) if str(x).strip()]
        if turn_id not in src:
            src.append(turn_id)
        out["source_turn_ids"] = src
    if not out.get("type"):
        user_query = str(req.get("user_query") or "")
        assistant_final = str(req.get("assistant_final") or "")
        out["type"] = _infer_semantic_bead_type(user_query, assistant_final)
    if not out.get("tags"):
        out["tags"] = ["crawler_reviewed", "turn_finalized"]
    return out


def _ensure_turn_creation_update(root: str, req: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Guarantee one current-turn creation candidate exists for canonical per-turn bead write."""
    out = dict(updates or {})
    key = "beads_create"
    rows = list(out.get(key) or [])
    turn_id = str(req.get("turn_id") or "")

    has_turn = False
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rows[i] = _enforce_turn_row_invariants(root, req, row)
        src = [str(x) for x in (rows[i].get("source_turn_ids") or []) if str(x)]
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

    mark_turn_checkpoint(root, turn_id=req["turn_id"])

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

    reviewed_updates, gate = _resolve_reviewed_updates(req)
    if gate.get("blocked"):
        return {
            "ok": False,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 1,
            "error_code": str(gate.get("error_code") or ERROR_AGENT_UPDATES_MISSING),
            "error": "agent-authored crawler updates required",
            "emitted": emitted,
            "crawler_handoff": {
                "required": True,
                "agent_authored_gate": gate,
            },
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
    if not isinstance(reviewed_updates, dict):
        reviewed_updates = _default_crawler_updates(req)
    reviewed_updates = _ensure_turn_creation_update(root, req, reviewed_updates)

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
    # them to queued associations and merge at the per-turn boundary.
    preview_queued = _queue_preview_associations(root=root, session_id=req["session_id"], visible_bead_ids=visible_ids)

    turn_merge = merge_crawler_updates(root=root, session_id=req["session_id"])

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
            "agent_authored_gate": gate,
            "context_visible_count": len(visible_ids),
            "auto_apply": auto_apply,
            "preview_association_queued": int(preview_queued),
            "turn_merge": {
                "ok": bool(turn_merge.get("ok", True)),
                "merged": int(turn_merge.get("merged") or 0),
                "promotions_marked": int(turn_merge.get("promotions_marked") or 0),
                "associations_appended": int(turn_merge.get("associations_appended") or 0),
            },
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


def _upsert_process_flush_checkpoint_bead(
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
    state = _read_flush_state(root)
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
    checkpoint_bead_id, checkpoint_created = _upsert_process_flush_checkpoint_bead(
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
    state = _read_flush_state(root)
    sessions = state.setdefault("sessions", {}) if isinstance(state, dict) else {}
    if isinstance(sessions, dict):
        sessions[str(session_id)] = {
            "last_flushed_turn_id": str(flush_anchor_turn or ""),
            "last_flush_tx_id": flush_id_final,
            "last_flush_source": str(source or "flush_hook"),
            "last_seen_turn_id": str(latest_turn or ""),
            "last_seen_turn_status": str(latest_turn_status or "unknown"),
        }
        _write_flush_state(root, state)

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


def _find_existing_session_start_bead(root: str, session_id: str) -> dict[str, Any] | None:
    store = MemoryStore(root=root)
    try:
        idx = store._read_json(Path(root) / ".beads" / "index.json")
        beads = idx.get("beads") or {}
        matches: list[dict[str, Any]] = []
        for bead in beads.values():
            if str((bead or {}).get("session_id") or "") != str(session_id):
                continue
            if str((bead or {}).get("type") or "") != "session_start":
                continue
            matches.append(dict(bead))
        if not matches:
            return None
        matches.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
        return matches[0]
    finally:
        store.close()


def _build_session_start_snapshot(*, session_id: str, continuity: dict[str, Any], max_items: int) -> dict[str, Any]:
    raw_records = list(continuity.get("records") or [])

    def _is_session_start_record(rec: dict[str, Any]) -> bool:
        typ = str((rec or {}).get("type") or "").strip().lower()
        if typ == "session_start":
            return True
        tags = {str(t).strip().lower() for t in ((rec or {}).get("tags") or []) if str(t).strip()}
        return "session_start" in tags

    filtered_records = [r for r in raw_records if not _is_session_start_record(r)]
    filtered_session_start_count = max(0, len(raw_records) - len(filtered_records))
    records = filtered_records[: max(1, int(max_items))]
    authority = str(continuity.get("authority") or "unknown")
    included_bead_ids = [str(x) for x in (continuity.get("included_bead_ids") or []) if str(x).strip()]
    meta = dict(continuity.get("meta") or {})

    source_turn_ids: list[str] = []
    for rec in records:
        for tid in (rec.get("source_turn_ids") or []):
            stid = str(tid or "").strip()
            if stid and stid not in source_turn_ids:
                source_turn_ids.append(stid)
    if not source_turn_ids:
        source_turn_ids = [f"session-start:{session_id}"]

    summary = [
        f"Continuity authority: {authority}",
        f"Carried records: {len(records)}",
        f"Included bead refs: {len(included_bead_ids)}",
    ]
    if filtered_session_start_count:
        summary.append(f"Filtered prior session_start records: {filtered_session_start_count}")
    for rec in records[:3]:
        r_type = str(rec.get("type") or "memory")
        r_title = str(rec.get("title") or "").strip() or r_type
        summary.append(f"{r_type}: {r_title}")

    detail_lines = [
        "Session-start continuity snapshot.",
        f"session_id={session_id}",
        f"authority={authority}",
        f"record_count={len(records)}",
        f"included_bead_ids={','.join(included_bead_ids) if included_bead_ids else '-'}",
    ]
    if filtered_session_start_count:
        detail_lines.append(f"filtered_prior_session_start_records={filtered_session_start_count}")
    if meta:
        detail_lines.append(f"meta={json.dumps(meta, ensure_ascii=False, sort_keys=True)}")
    for i, rec in enumerate(records, 1):
        r_type = str(rec.get("type") or "memory")
        r_title = str(rec.get("title") or "")
        r_summary = rec.get("summary")
        if isinstance(r_summary, list):
            r_summary = " ".join(str(x) for x in r_summary)
        r_summary = str(r_summary or rec.get("detail") or "").strip()
        detail_lines.append(f"{i}. [{r_type}] {r_title}: {r_summary}")

    return {
        "type": "session_start",
        "title": "Session start",
        "summary": summary[:8],
        "detail": "\n".join(detail_lines),
        "source_turn_ids": source_turn_ids,
        "tags": ["session_start", "lifecycle_boundary", "continuity_snapshot"],
        "retrieval_eligible": True,
        "continuity_authority": authority,
        "continuity_record_count": len(records),
        "continuity_filtered_session_start_count": filtered_session_start_count,
        "continuity_included_bead_ids": included_bead_ids,
    }


def process_session_start(
    *,
    root: str,
    session_id: str,
    source: str = "runtime",
    max_items: int = 80,
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_session_id"}

    existing = _find_existing_session_start_bead(root=root, session_id=sid)
    if existing:
        return {
            "ok": True,
            "created": False,
            "bead_id": str(existing.get("id") or ""),
            "session_id": sid,
            "source": source,
            "type": "session_start",
        }

    continuity = load_continuity_injection(workspace_root=root, max_items=max_items)
    bead = _build_session_start_snapshot(session_id=sid, continuity=continuity, max_items=max_items)

    store = MemoryStore(root=root)
    try:
        bead_id = store.add_bead(
            type="session_start",
            title=str(bead.get("title") or "Session start"),
            summary=list(bead.get("summary") or []),
            detail=str(bead.get("detail") or ""),
            session_id=sid,
            source_turn_ids=list(bead.get("source_turn_ids") or []),
            tags=list(bead.get("tags") or []),
            retrieval_eligible=bool(bead.get("retrieval_eligible", True)),
            continuity_authority=str(bead.get("continuity_authority") or ""),
            continuity_record_count=int(bead.get("continuity_record_count") or 0),
            continuity_filtered_session_start_count=int(bead.get("continuity_filtered_session_start_count") or 0),
            continuity_included_bead_ids=list(bead.get("continuity_included_bead_ids") or []),
            created_by_source=str(source or "runtime"),
        )
    finally:
        store.close()

    return {
        "ok": True,
        "created": True,
        "bead_id": bead_id,
        "session_id": sid,
        "source": source,
        "type": "session_start",
        "authority": continuity.get("authority"),
        "record_count": int(bead.get("continuity_record_count") or 0),
    }


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out
