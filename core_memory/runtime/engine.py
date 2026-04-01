from __future__ import annotations

import json
import uuid
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
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
from ..policy.hygiene import enforce_bead_hygiene_contract, is_runtime_meta_chatter
from ..retrieval.lifecycle import mark_turn_checkpoint, mark_flush_checkpoint

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


def _latest_session_bead_id(root: str, session_id: str) -> str | None:
    s = MemoryStore(root=root)
    idx = s._read_json(Path(root) / ".beads" / "index.json")
    latest = None
    latest_ts = ""
    for bid, bead in (idx.get("beads") or {}).items():
        if str((bead or {}).get("session_id") or "") != str(session_id):
            continue
        ts = str((bead or {}).get("created_at") or "")
        if ts >= latest_ts:
            latest_ts = ts
            latest = str(bid)
    return latest


def _build_narrative_fields(user_query: str, assistant_final: str) -> tuple[str, list[str], str]:
    uq = str(user_query or "").strip()
    af = str(assistant_final or "").strip()
    detail = (af or uq or "").strip()[:1200]

    # Build compact sentence candidates.
    sentences = [s.strip() for s in re.split(r"[\n\.!?]+", detail) if s.strip()]
    first = (sentences[0] if sentences else (af or uq or "assistant turn")).strip()

    # Prefer causal headline when available.
    causal_cues = ["because", "due to", "so that", "to avoid", "therefore", "which enabled", "enabled", "caused", "blocked", "unblocked", "refined"]
    causal_line = ""
    for s in sentences[:6]:
        low = s.lower()
        if any(c in low for c in causal_cues):
            causal_line = s
            break

    title_src = causal_line or first
    title_src = re.sub(r"^\s*\*+\s*", "", title_src)
    title = title_src[:160] if title_src else "assistant turn"

    summary: list[str] = []
    if first:
        summary.append(first[:220])  # what changed / decision
    if causal_line and causal_line != first:
        summary.append(causal_line[:220])  # why
    # outcome/impact hint
    for s in sentences[1:8]:
        low = s.lower()
        if any(k in low for k in ["result", "outcome", "completed", "resolved", "now", "ready", "shipped", "verified"]):
            if s[:220] not in summary:
                summary.append(s[:220])
            break
    if not summary and detail:
        summary = [detail[:220]]

    # Keep compact + deterministic
    deduped: list[str] = []
    seen = set()
    for s in summary:
        k = s.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(s.strip())
    return title, deduped[:3], detail


def _enforce_turn_row_invariants(root: str, req: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Repair required turn-row fields after hook/crawler mutations.

    This is adapter-safe (OpenClaw/PydanticAI): it never hard-fails hooks;
    it only fills missing critical fields.
    """
    out = dict(row or {})
    uq = str(req.get("user_query") or "")
    af = str(req.get("assistant_final") or "")
    title, summary, detail = _build_narrative_fields(uq, af)
    retrieval_eligible = not is_runtime_meta_chatter(user_query=uq, assistant_final=af)

    out.setdefault("type", _infer_semantic_bead_type(uq, af))
    out["title"] = str(out.get("title") or title or "assistant turn")[:160]

    existing_summary = out.get("summary")
    if not isinstance(existing_summary, list) or not [str(x).strip() for x in existing_summary if str(x).strip()]:
        out["summary"] = summary

    if not str(out.get("detail") or "").strip():
        out["detail"] = detail

    if not isinstance(out.get("source_turn_ids"), list) or not out.get("source_turn_ids"):
        out["source_turn_ids"] = [str(req.get("turn_id") or "")]

    md = req.get("metadata") or {}
    if out.get("turn_index") is None:
        out["turn_index"] = int(md.get("turn_index") or 0) or None
    if out.get("prev_bead_id") is None:
        out["prev_bead_id"] = _latest_session_bead_id(root=root, session_id=str(req.get("session_id") or ""))

    # Preserve/repair retrieval enrichment fields
    out["retrieval_eligible"] = bool(out.get("retrieval_eligible", retrieval_eligible))
    if out.get("retrieval_eligible"):
        if not str(out.get("retrieval_title") or "").strip():
            out["retrieval_title"] = str(out.get("title") or title)[:160]
        rf = out.get("retrieval_facts")
        if not isinstance(rf, list) or not [str(x).strip() for x in rf if str(x).strip()]:
            out["retrieval_facts"] = (summary[:2] if summary else ([detail[:240]] if detail else []))

    # Ensure because list shape (for promotion quality gate)
    because = out.get("because")
    if because is None:
        because = [uq[:240]] if uq.strip() else []
    if not isinstance(because, list):
        because = [str(because)] if str(because).strip() else []
    out["because"] = [str(x).strip() for x in because if str(x).strip()][:5]

    tags = [str(x) for x in (out.get("tags") or []) if str(x)]
    if "narrative_essence" not in tags:
        tags.append("narrative_essence")
    out["tags"] = tags[:15]

    return enforce_bead_hygiene_contract(out)


def _default_crawler_updates(root: str, req: dict[str, Any]) -> dict[str, Any]:
    user_query = str(req.get("user_query") or "")
    assistant_final = str(req.get("assistant_final") or "")
    title, summary, detail = _build_narrative_fields(user_query, assistant_final)

    # Thin-by-default for runtime/meta chatter; richer turns can be upgraded by crawler/hygiene.
    retrieval_eligible = not is_runtime_meta_chatter(user_query=user_query, assistant_final=assistant_final)

    row = {
        "type": _infer_semantic_bead_type(user_query, assistant_final),
        "title": title or "assistant turn",
        "summary": summary,
        "because": ([user_query[:240]] if user_query.strip() else []),
        "source_turn_ids": [str(req.get("turn_id") or "")],
        "turn_index": int((req.get("metadata") or {}).get("turn_index") or 0) or None,
        "prev_bead_id": _latest_session_bead_id(root=root, session_id=str(req.get("session_id") or "")),
        "tags": ["crawler_reviewed", "turn_finalized", "narrative_essence"],
        "detail": (assistant_final[:1200] if assistant_final else detail),
        "retrieval_eligible": bool(retrieval_eligible),
        "retrieval_title": (title[:160] if retrieval_eligible else None),
        "retrieval_facts": (summary[:2] if retrieval_eligible and summary else ([detail[:240]] if retrieval_eligible and detail else [])),
    }
    row = _enforce_turn_row_invariants(root, req, row)
    return {"beads_create": [row]}


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
        uq = str(req.get("user_query") or "")
        af = str(req.get("assistant_final") or "")
        title, summary, detail = _build_narrative_fields(uq, af)
        retrieval_eligible = not is_runtime_meta_chatter(
            user_query=uq,
            assistant_final=af,
        )
        rows.append(
            _enforce_turn_row_invariants(
                root,
                req,
                {
                    "type": _infer_semantic_bead_type(uq, af),
                    "title": title or "assistant turn",
                    "summary": summary,
                    "because": ([uq[:240]] if uq.strip() else []),
                    "source_turn_ids": [turn_id],
                    "turn_index": int((req.get("metadata") or {}).get("turn_index") or 0) or None,
                    "prev_bead_id": _latest_session_bead_id(root=root, session_id=str(req.get("session_id") or "")),
                    "tags": ["crawler_reviewed", "turn_finalized", "seeded_by_engine", "narrative_essence"],
                    "detail": (af[:1200] if af else detail),
                    "retrieval_eligible": bool(retrieval_eligible),
                    "retrieval_title": (title[:160] if retrieval_eligible else None),
                    "retrieval_facts": (summary[:2] if retrieval_eligible and summary else ([detail[:240]] if retrieval_eligible and detail else [])),
                },
            )
        )

    out[key] = rows
    return out


def _queue_preview_associations(root: str, session_id: str, visible_bead_ids: list[str]) -> int:
    """Promote association_preview candidates to queued association appends for flush commit."""
    store = MemoryStore(root=root)
    idx = store._read_json(Path(root) / ".beads" / "index.json")
    beads = idx.get("beads") or {}
    log_path = _crawler_updates_log_path(root, session_id)
    now = datetime.now(timezone.utc).isoformat()

    existing_keys: set[tuple[str, str]] = set()
    for a in (idx.get("associations") or []):
        src = str(a.get("source_bead") or a.get("source_bead_id") or "")
        tgt = str(a.get("target_bead") or a.get("target_bead_id") or "")
        if src and tgt:
            existing_keys.add((src, tgt))

    visible = set([str(x) for x in (visible_bead_ids or []) if str(x)])
    queued = 0
    for bid, bead in beads.items():
        if str((bead or {}).get("session_id") or "") != str(session_id):
            continue
        if visible and str(bid) not in visible:
            continue
        previews = (bead or {}).get("association_preview") or []
        for preview in previews:
            target_id = str((preview or {}).get("bead_id") or "")
            if not target_id or target_id not in beads:
                continue
            if (str(bid), target_id) in existing_keys or (target_id, str(bid)) in existing_keys:
                continue
            rel = str((preview or {}).get("relationship") or "related_to")
            append_jsonl(
                log_path,
                {
                    "schema": "openclaw.memory.crawler_update.v1",
                    "kind": "association_append",
                    "session_id": str(session_id),
                    "id": f"assoc-{uuid.uuid4().hex[:12].upper()}",
                    "source_bead": str(bid),
                    "target_bead": target_id,
                    "relationship": rel,
                    "edge_class": "preview_promoted",
                    "confidence": float((preview or {}).get("score") or 0.0),
                    "created_at": now,
                },
            )
            existing_keys.add((str(bid), target_id))
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
        reviewed_updates = _default_crawler_updates(root, req)
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

    # Recompute visibility after association pass may have created/updated beads.
    session_visible_after = _session_visible_bead_ids(root=root, session_id=req["session_id"])
    visible_ids = sorted(set(visible_ids + session_visible_after))

    # Promote store-written association previews into queued association appends for flush commit.
    preview_queued = _queue_preview_associations(root=root, session_id=req["session_id"], visible_bead_ids=visible_ids)

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
            "preview_association_queued": int(preview_queued),
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


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out
