from __future__ import annotations

import json
import uuid
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from core_memory.integrations.openclaw_flags import (
    agent_min_semantic_associations_after_first,
    claim_layer_enabled,
    preview_association_allow_shared_tag,
    preview_association_promotion_enabled,
    resolved_agent_authored_gate,
)
from core_memory.claim.turn_integration import extract_and_attach_claims
from core_memory.claim.outcomes import classify_memory_outcome
from core_memory.claim.update_policy import emit_claim_updates
from core_memory.persistence.store_claim_ops import write_memory_outcome_to_bead
from ..association.crawler_contract import (
    build_crawler_context,
    merge_crawler_updates,
    _crawler_updates_log_path,
)
from .association_pass import run_association_pass
from ..write_pipeline.continuity_injection import load_continuity_injection
from .state import mark_memory_pass, try_claim_memory_pass
from .ingress import maybe_emit_finalize_memory_event
from .worker import SidecarPolicy, process_memory_event
from ..write_pipeline.orchestrate import run_consolidate_pipeline
from ..persistence.io_utils import append_jsonl
from ..persistence.store import MemoryStore
from .decision_pass import run_session_decision_pass
from ..policy.hygiene import enforce_bead_hygiene_contract, is_runtime_meta_chatter
from ..retrieval.lifecycle import mark_turn_checkpoint
from .agent_crawler_invoke import invoke_turn_crawler_agent
from .agent_authored_contract import (
    ERROR_AGENT_CALLABLE_MISSING,
    ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
    ERROR_AGENT_UPDATES_INVALID,
    ERROR_AGENT_INVOCATION_EXHAUSTED,
    ERROR_AGENT_UPDATES_MISSING,
    validate_agent_authored_updates,
)
from .turn_prep import normalize_turn_request as _normalize_turn_request, infer_semantic_bead_type as _infer_semantic_bead_type
from .session_start_flow import process_session_start_impl
from .turn_quality import emit_agent_turn_quality_metric as _emit_agent_turn_quality_metric
from .flush_flow import process_flush_impl
from .turn_flow import process_turn_finalized_impl

logger = logging.getLogger(__name__)


# Canonical runtime center.
# P6A Step 2: deepen ownership by making memory_engine responsible for
# orchestration while delegating phase internals to dedicated lifecycle modules.


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
    entities = _default_entities_from_text(user_query, assistant_final)
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
                "entities": entities,
                "tags": ["crawler_reviewed", "turn_finalized"],
                "detail": assistant_final[:1200] if assistant_final else summary[:1200],
            }
        ]
    }


def _default_entities_from_text(*texts: str, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    stop = {
        "Before", "After", "Turn", "Act", "Show", "Open", "Record", "Add", "Explain",
        "What", "When", "Where", "Which", "Why", "How", "Claims", "Graph", "Entities",
        "Runtime", "Benchmark", "Send", "Point", "Say",
    }
    low_stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "your", "our", "their",
        "were", "was", "have", "has", "had", "will", "would", "should", "could", "can", "cant",
        "about", "after", "before", "then", "than", "because", "there", "here", "when", "where",
        "what", "which", "who", "whom", "whose", "why", "how", "turn", "main", "session",
    }
    for raw in texts:
        text = str(raw or "")
        if not text:
            continue
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9._-]{2,}|[A-Z]{2,}[A-Za-z0-9._-]*)\b", text):
            token = str(m.group(1) or "").strip().strip(".,:;!?()[]{}\"'")
            if len(token) < 3:
                continue
            if token in stop:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= max(1, int(limit)):
                return out

    if out:
        return out

    for raw in texts:
        text = str(raw or "")
        if not text:
            continue
        for token in re.finditer(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", text):
            value = str(token.group(0) or "").strip().strip(".,:;!?()[]{}\"'")
            key = value.lower()
            if key in low_stop:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(value)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _resolve_reviewed_updates(
    req: dict[str, Any],
    *,
    source_override: str | None = None,
    invocation_diag: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    md = req.get("metadata") or {}
    reviewed = md.get("crawler_updates") if isinstance(md, dict) else None
    resolved_gate = resolved_agent_authored_gate()
    required = bool(resolved_gate.get("required"))
    fail_open = bool(resolved_gate.get("fail_open"))
    mode = str(resolved_gate.get("mode") or "observe")

    gate = {
        "required": bool(required),
        "fail_open": bool(fail_open),
        "mode": mode,
        "source": str(source_override or ("metadata.crawler_updates" if isinstance(reviewed, dict) and reviewed else "default_fallback")),
        "used_fallback": False,
        "blocked": False,
        "error_code": None,
        "agent_invocation": dict(invocation_diag or {}),
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
        if isinstance(invocation_diag, dict) and invocation_diag.get("error_code") in {
            ERROR_AGENT_INVOCATION_EXHAUSTED,
            ERROR_AGENT_CALLABLE_MISSING,
        }:
            gate["error_code"] = str(invocation_diag.get("error_code") or ERROR_AGENT_UPDATES_MISSING)
        else:
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
    if not preview_association_promotion_enabled():
        return 0

    allow_shared_tag = preview_association_allow_shared_tag()

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
            if rel == "shared_tag" and not allow_shared_tag:
                continue
            if rel == "precedes":
                continue
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


def _non_temporal_semantic_association_count(updates: dict[str, Any]) -> int:
    associations = list((updates or {}).get("associations") or [])
    excluded = {"follows", "precedes", "shared_tag", "associated_with"}
    count = 0
    for row in associations:
        if not isinstance(row, dict):
            continue
        rel = str(row.get("relationship") or "").strip().lower()
        if rel and rel not in excluded:
            count += 1
    return count


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
    """Canonical turn-finalized boundary entrypoint (implementation delegated)."""
    result = process_turn_finalized_impl(
        root=root,
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
        policy=policy,
        normalize_turn_request=_normalize_turn_request,
        mark_turn_checkpoint=mark_turn_checkpoint,
        maybe_emit_finalize_memory_event=maybe_emit_finalize_memory_event,
        build_crawler_context=build_crawler_context,
        invoke_turn_crawler_agent=invoke_turn_crawler_agent,
        resolve_reviewed_updates=_resolve_reviewed_updates,
        emit_agent_turn_quality_metric=_emit_agent_turn_quality_metric,
        session_visible_bead_ids=_session_visible_bead_ids,
        non_temporal_semantic_association_count=_non_temporal_semantic_association_count,
        agent_min_semantic_associations_after_first=agent_min_semantic_associations_after_first,
        try_claim_memory_pass=try_claim_memory_pass,
        mark_memory_pass=mark_memory_pass,
        process_memory_event=process_memory_event,
        default_crawler_updates=_default_crawler_updates,
        ensure_turn_creation_update=_ensure_turn_creation_update,
        run_association_pass=run_association_pass,
        queue_preview_associations=_queue_preview_associations,
        merge_crawler_updates=merge_crawler_updates,
        run_session_decision_pass=run_session_decision_pass,
        error_agent_updates_missing=ERROR_AGENT_UPDATES_MISSING,
        error_agent_semantic_coverage_missing=ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
        logger=logger,
        extract_and_attach_claims_fn=extract_and_attach_claims if claim_layer_enabled() else None,
        emit_claim_updates_fn=emit_claim_updates if claim_layer_enabled() else None,
        classify_memory_outcome_fn=classify_memory_outcome if claim_layer_enabled() else None,
        write_memory_outcome_to_bead_fn=write_memory_outcome_to_bead if claim_layer_enabled() else None,
    )

    # F-W1: drain enrichment queue after critical path returns
    if result.get("ok") and result.get("enrichment_queued"):
        try:
            from core_memory.runtime.side_effect_queue import drain_side_effect_queue
            drain_side_effect_queue(root=root, max_items=1)
        except Exception as exc:
            logger.warning("engine: enrichment drain failed (bead already persisted): %s", exc)

    return result


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
    """Canonical session-flush boundary entrypoint (implementation delegated)."""
    return process_flush_impl(
        root=root,
        session_id=session_id,
        promote=promote,
        token_budget=token_budget,
        max_beads=max_beads,
        source=source,
        flush_tx_id=flush_tx_id,
    )


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


def process_session_start(
    *,
    root: str,
    session_id: str,
    source: str = "runtime",
    max_items: int = 80,
) -> dict[str, Any]:
    """Canonical session-start boundary entrypoint (implementation delegated)."""
    return process_session_start_impl(root=root, session_id=session_id, source=source, max_items=max_items)


def continuity_injection_context(*, workspace_root: str, max_items: int = 80) -> dict[str, Any]:
    out = load_continuity_injection(workspace_root=workspace_root, max_items=max_items)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "continuity_injection_context"})
    return out
