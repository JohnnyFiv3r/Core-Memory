from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def process_turn_finalized_impl(
    *,
    root: str,
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
    policy: Any,
    normalize_turn_request: Callable[..., dict[str, Any]],
    mark_turn_checkpoint: Callable[..., Any],
    maybe_emit_finalize_memory_event: Callable[..., dict[str, Any]],
    build_crawler_context: Callable[..., dict[str, Any]],
    invoke_turn_crawler_agent: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    resolve_reviewed_updates: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    emit_agent_turn_quality_metric: Callable[..., None],
    session_visible_bead_ids: Callable[..., list[str]],
    non_temporal_semantic_association_count: Callable[[dict[str, Any]], int],
    agent_min_semantic_associations_after_first: Callable[[], int],
    try_claim_memory_pass: Callable[..., tuple[bool, dict[str, Any]]],
    mark_memory_pass: Callable[..., Any],
    process_memory_event: Callable[..., dict[str, Any]],
    default_crawler_updates: Callable[[dict[str, Any]], dict[str, Any]],
    ensure_turn_creation_update: Callable[..., dict[str, Any]],
    run_association_pass: Callable[..., dict[str, Any]],
    queue_preview_associations: Callable[..., int],
    merge_crawler_updates: Callable[..., dict[str, Any]],
    run_session_decision_pass: Callable[..., dict[str, Any]],
    error_agent_updates_missing: str,
    error_agent_semantic_coverage_missing: str,
    logger: Any,
    extract_and_attach_claims_fn: Callable[..., dict[str, Any]] | None = None,
    emit_claim_updates_fn: Callable[..., list[dict[str, Any]]] | None = None,
    classify_memory_outcome_fn: Callable[..., dict[str, Any] | None] | None = None,
    write_memory_outcome_to_bead_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    req = normalize_turn_request(
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

    crawler_ctx = build_crawler_context(root=root, session_id=req["session_id"], limit=200)
    invoked_updates, invocation_diag = invoke_turn_crawler_agent(
        root=root,
        req=req,
        crawler_context=crawler_ctx,
    )

    req_for_updates = dict(req)
    md_for_updates = dict(req.get("metadata") or {})
    source_override = None
    if isinstance(md_for_updates.get("crawler_updates"), dict) and md_for_updates.get("crawler_updates"):
        source_override = "metadata.crawler_updates"
    elif isinstance(invoked_updates, dict) and invoked_updates:
        md_for_updates["crawler_updates"] = dict(invoked_updates)
        source_override = "agent_callable"
    req_for_updates["metadata"] = md_for_updates

    reviewed_updates, gate = resolve_reviewed_updates(
        req_for_updates,
        source_override=source_override,
        invocation_diag=invocation_diag,
    )
    if gate.get("blocked"):
        emit_agent_turn_quality_metric(
            root=root,
            req=req,
            gate=gate,
            updates=reviewed_updates,
            result="blocked",
            error_code=str(gate.get("error_code") or error_agent_updates_missing),
        )
        return {
            "ok": False,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 1,
            "error_code": str(gate.get("error_code") or error_agent_updates_missing),
            "error": "agent-authored crawler updates required",
            "emitted": emitted,
            "crawler_handoff": {
                "required": True,
                "agent_authored_gate": gate,
            },
            "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
        }

    # F-W2: gate severity — hard mode blocks, warn mode flags, off mode skips
    _structural_coverage_missing = False
    if bool(gate.get("required")) and isinstance(reviewed_updates, dict):
        prior_beads = session_visible_bead_ids(root=root, session_id=req["session_id"])
        min_required = int(agent_min_semantic_associations_after_first())
        semantic_count = int(non_temporal_semantic_association_count(reviewed_updates))
        gate["semantic_policy"] = {
            "prior_bead_count": len(prior_beads),
            "min_required_after_first": min_required,
            "semantic_assoc_count": semantic_count,
        }
        if len(prior_beads) >= 1 and semantic_count < min_required:
            if not bool(gate.get("fail_open")):
                # hard mode: block the turn
                gate["blocked"] = True
                gate["error_code"] = error_agent_semantic_coverage_missing
                emit_agent_turn_quality_metric(
                    root=root,
                    req=req,
                    gate=gate,
                    updates=reviewed_updates,
                    result="blocked",
                    error_code=error_agent_semantic_coverage_missing,
                )
                return {
                    "ok": False,
                    "mode": "turn",
                    "authority_path": "canonical_in_process",
                    "processed": 0,
                    "failed": 1,
                    "error_code": error_agent_semantic_coverage_missing,
                    "error": "insufficient non-temporal semantic associations for non-initial turn",
                    "emitted": emitted,
                    "crawler_handoff": {
                        "required": True,
                        "agent_authored_gate": gate,
                    },
                    "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
                }
            else:
                # warn mode: flag the bead but proceed
                _structural_coverage_missing = True
                gate["warned"] = True
                logger.warning(
                    "agent-authored gate: structural coverage missing (warn mode), "
                    "session=%s turn=%s semantic_count=%d min_required=%d",
                    req.get("session_id"), req.get("turn_id"), semantic_count, min_required,
                )

    claimed, state_after = try_claim_memory_pass(Path(root), req["session_id"], req["turn_id"])
    if not claimed:
        emit_agent_turn_quality_metric(
            root=root,
            req=req,
            gate=gate,
            updates=reviewed_updates,
            result="not_claimed",
        )
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
        emit_agent_turn_quality_metric(
            root=root,
            req=req,
            gate=gate,
            updates=reviewed_updates,
            result="error",
            error_code="direct_turn_exception",
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

    # F-W2: flag the bead in warn mode if coverage was insufficient
    if _structural_coverage_missing:
        bead_id = str((delta or {}).get("bead_id") or "")
        if bead_id:
            from core_memory.persistence.store import MemoryStore
            store = MemoryStore(root)
            idx = store._read_json(store.beads_dir / "index.json")
            bead = (idx.get("beads") or {}).get(bead_id)
            if bead:
                bead["structural_coverage_missing"] = True
                store._write_json(store.beads_dir / "index.json", idx)

    if not isinstance(reviewed_updates, dict):
        reviewed_updates = default_crawler_updates(req)
    reviewed_updates = ensure_turn_creation_update(root, req, reviewed_updates)

    # F-W1: enqueue enrichment stages instead of running them inline.
    # The bead is already persisted — enrichment is post-commit.
    from core_memory.runtime.enrichment import enqueue_turn_enrichment, _enrichment_queue_enabled

    bead_id = str((delta or {}).get("bead_id") or "")
    enrichment_queued = False

    if _enrichment_queue_enabled():
        enqueue_result = enqueue_turn_enrichment(
            root=root,
            session_id=req["session_id"],
            turn_id=req["turn_id"],
            bead_id=bead_id,
            req=req,
            reviewed_updates=reviewed_updates,
            crawler_ctx=crawler_ctx,
        )
        enrichment_queued = bool(enqueue_result and enqueue_result.get("ok"))

    if not enrichment_queued:
        # Fallback: run enrichment stages inline (pre-F-W1 behavior)
        crawler_visible = list(crawler_ctx.get("visible_bead_ids") or [])
        session_visible = session_visible_bead_ids(root=root, session_id=req["session_id"])
        visible_ids = sorted(set(crawler_visible + session_visible))

        auto_apply = run_association_pass(
            root=root,
            session_id=req["session_id"],
            updates=reviewed_updates,
            visible_bead_ids=visible_ids,
        )

        session_visible_after = session_visible_bead_ids(root=root, session_id=req["session_id"])
        visible_ids = sorted(set(crawler_visible + session_visible_after))

        claim_telemetry: dict[str, Any] = {}
        if extract_and_attach_claims_fn is not None:
            created_bead_ids = list(auto_apply.get("created_bead_ids") or [])
            claim_telemetry = extract_and_attach_claims_fn(
                root, req["session_id"], req["turn_id"], created_bead_ids, req,
            ) or {}

        preview_queued = queue_preview_associations(root=root, session_id=req["session_id"], visible_bead_ids=visible_ids)
        turn_merge = merge_crawler_updates(root=root, session_id=req["session_id"])

        decision_pass = run_session_decision_pass(
            root=root, session_id=req["session_id"],
            visible_bead_ids=visible_ids, turn_id=req["turn_id"],
        )

        claim_updates_emitted = 0
        canonical_turn_bead_id = str(claim_telemetry.get("canonical_bead_id") or "")
        claims_batch = list(claim_telemetry.get("claims_batch") or [])
        if emit_claim_updates_fn is not None and canonical_turn_bead_id and claims_batch:
            claim_updates = emit_claim_updates_fn(
                root, claims_batch, canonical_turn_bead_id,
                session_id=req["session_id"], visible_bead_ids=visible_ids,
                reviewed_updates=reviewed_updates, decision_pass=decision_pass,
            ) or []
            claim_updates_emitted = len(claim_updates)

        memory_outcome_written = False
        if classify_memory_outcome_fn is not None and canonical_turn_bead_id:
            md = dict(req.get("metadata") or {})
            context_beads = list(md.get("retrieved_beads") or md.get("context_beads") or req.get("window_bead_ids") or [])
            turn_context = {
                "retrieved_beads": context_beads,
                "query": str(req.get("user_query") or ""),
                "used_memory": bool(md.get("used_memory")) or bool(context_beads),
                "correction_triggered": bool(md.get("correction_triggered") or md.get("memory_correction")),
                "reflection_triggered": bool(md.get("reflection_triggered") or md.get("memory_reflection")),
            }
            outcome = classify_memory_outcome_fn(turn_context)
            if isinstance(outcome, dict) and write_memory_outcome_to_bead_fn is not None:
                write_memory_outcome_to_bead_fn(
                    root, canonical_turn_bead_id,
                    interaction_role=outcome.get("interaction_role"),
                    memory_outcome=outcome.get("memory_outcome"),
                )
                memory_outcome_written = True

        emit_agent_turn_quality_metric(
            root=root, req=req, gate=gate, updates=reviewed_updates,
            result="success",
        )

    out: dict[str, Any] = {
        "ok": True,
        "mode": "turn",
        "authority_path": "canonical_in_process",
        "processed": 1,
        "failed": 0,
        "delta": delta,
        "emitted": emitted,
        "enrichment_queued": enrichment_queued,
        "crawler_handoff": {
            "required": True,
            "agent_authored_gate": gate,
        },
        "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
    }
    return out
