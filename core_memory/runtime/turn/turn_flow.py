from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any, Callable


def process_turn_finalized_impl(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None,
    trace_id: str | None,
    turns: list[Any] | None,
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
        turns=turns,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )

    # Hard agent-authored failures should be side-effect-free: validate metadata
    # / missing-callable cases before semantic checkpoints, event writes, or store init.
    md_preflight = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
    reviewed_preflight = (md_preflight or {}).get("crawler_updates") if isinstance(md_preflight, dict) else None
    invocation_preflight: dict[str, Any] | None = None
    defer_preflight_to_contextual_agent = False
    source_preflight: str | None = "metadata.crawler_updates" if isinstance(reviewed_preflight, dict) and reviewed_preflight else None
    if not source_preflight:
        callable_path = str(os.environ.get("CORE_MEMORY_AGENT_CRAWLER_CALLABLE") or "").strip()
        if callable_path:
            try:
                if ":" not in callable_path:
                    raise ValueError("CORE_MEMORY_AGENT_CRAWLER_CALLABLE must be module:function")
                mod_name, fn_name = callable_path.split(":", 1)
                fn = getattr(importlib.import_module(mod_name.strip()), fn_name.strip(), None)
                if not callable(fn):
                    raise ValueError(f"callable not found: {callable_path}")
            except Exception as exc:
                invocation_preflight = {
                    "attempted": True,
                    "ok": False,
                    "source": "agent_callable",
                    "attempts": 0,
                    "error_code": "agent_callable_missing",
                    "reason": "invalid_CORE_MEMORY_AGENT_CRAWLER_CALLABLE",
                    "error": str(exc),
                    "callable": callable_path,
                }
            else:
                # A valid callable may require crawler_context; let the normal
                # post-checkpoint path invoke it with full context rather than
                # calling it here with an empty one.
                defer_preflight_to_contextual_agent = True
        else:
            invoked_preflight, invocation_preflight = invoke_turn_crawler_agent(root=root, req=req, crawler_context={})
            if isinstance(invoked_preflight, dict) and invoked_preflight:
                md_copy = dict(req.get("metadata") or {})
                md_copy["crawler_updates"] = dict(invoked_preflight)
                md_copy["_crawler_updates_source"] = "agent_callable"
                req["metadata"] = md_copy
                source_preflight = "agent_callable"
    if not defer_preflight_to_contextual_agent:
        reviewed_probe, gate_probe = resolve_reviewed_updates(
            req,
            source_override=source_preflight,
            invocation_diag=invocation_preflight,
            max_create_per_turn=getattr(policy, "max_create_per_turn", None),
        )
        if gate_probe.get("blocked"):
            contract_error = {"code": str(gate_probe.get("error_code") or error_agent_updates_missing), "details": dict(gate_probe.get("validation") or {})}
            return {
                "ok": False,
                "mode": "turn",
                "authority_path": "canonical_in_process",
                "processed": 0,
                "failed": 1,
                "error_code": str(gate_probe.get("error_code") or error_agent_updates_missing),
                "error": "agent-authored crawler updates required",
                "agent_contract_error": contract_error,
                "crawler_handoff": {
                    "required": True,
                    "agent_authored_gate": gate_probe,
                },
                "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
            }

    mark_turn_checkpoint(root, turn_id=req["turn_id"])

    emitted = maybe_emit_finalize_memory_event(
        root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        turns=req["turns"],
        speakers=req["speakers"],
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

    gate: dict[str, Any] = {}
    reviewed_updates: dict[str, Any] | None = None

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

    # Current architecture target: write the canonical turn bead first, then crawl associations/promotions against post-write session state.
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
        source_override = str(md_for_updates.get("_crawler_updates_source") or "metadata.crawler_updates")
    elif isinstance(invoked_updates, dict) and invoked_updates:
        md_for_updates["crawler_updates"] = dict(invoked_updates)
        source_override = "agent_callable"
    req_for_updates["metadata"] = md_for_updates

    reviewed_updates, gate = resolve_reviewed_updates(
        req_for_updates,
        source_override=source_override,
        invocation_diag=invocation_diag,
        max_create_per_turn=getattr(policy, "max_create_per_turn", None),
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
        contract_error = {"code": str(gate.get("error_code") or error_agent_updates_missing), "details": dict(gate.get("validation") or {})}
        return {
            "ok": False,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 0,
            "failed": 1,
            "error_code": str(gate.get("error_code") or error_agent_updates_missing),
            "error": "agent-authored crawler updates required",
            "agent_contract_error": contract_error,
            "emitted": emitted,
            "crawler_handoff": {
                "required": True,
                "agent_authored_gate": gate,
            },
            "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
        }

    _structural_coverage_missing = False
    if not isinstance(reviewed_updates, dict):
        reviewed_updates = default_crawler_updates(req)
    reviewed_updates = ensure_turn_creation_update(root, req, reviewed_updates)

    # Rebuild post-write crawler context so association/promotion decisions operate on the bead-centric session surface.
    crawler_ctx = build_crawler_context(root=root, session_id=req["session_id"], limit=200)

    # F-W2: gate severity — hard mode blocks, warn mode flags, off mode skips
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
                contract_error = {"code": error_agent_semantic_coverage_missing, "details": dict(gate.get("semantic_policy") or {})}
                return {
                    "ok": False,
                    "mode": "turn",
                    "authority_path": "canonical_in_process",
                    "processed": 0,
                    "failed": 1,
                    "error_code": error_agent_semantic_coverage_missing,
                    "error": "insufficient non-temporal semantic associations for non-initial turn",
                    "agent_contract_error": contract_error,
                    "emitted": emitted,
                    "crawler_handoff": {
                        "required": True,
                        "agent_authored_gate": gate,
                    },
                    "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
                }
            else:
                _structural_coverage_missing = True
                gate["warned"] = True
                logger.warning(
                    "agent-authored gate: structural coverage missing (warn mode), "
                    "session=%s turn=%s semantic_count=%d min_required=%d",
                    req.get("session_id"), req.get("turn_id"), semantic_count, min_required,
                )

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

    # F-W1: enqueue enrichment stages instead of running them inline.
    # The bead is already persisted — enrichment is post-commit.
    from core_memory.runtime.passes.enrichment import enqueue_turn_enrichment, _enrichment_queue_enabled

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

    auto_apply: dict[str, Any] = {}
    preview_queued = 0
    turn_merge: dict[str, Any] = {}
    decision_pass: dict[str, Any] = {}
    claim_telemetry: dict[str, Any] = {}
    claim_updates_emitted = 0
    memory_outcome_written = False
    goal_lifecycle: dict[str, Any] = {}

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

        canonical_turn_bead_id = str(claim_telemetry.get("canonical_bead_id") or "")
        claims_batch = list(claim_telemetry.get("claims_batch") or [])
        if emit_claim_updates_fn is not None and canonical_turn_bead_id and claims_batch:
            claim_visible_ids = sorted(
                set(visible_ids + [str(x) for x in (req.get("window_bead_ids") or []) if str(x).strip()])
            )
            claim_updates = emit_claim_updates_fn(
                root, claims_batch, canonical_turn_bead_id,
                session_id=req["session_id"], visible_bead_ids=claim_visible_ids,
                reviewed_updates=reviewed_updates, decision_pass=decision_pass,
            ) or []
            claim_updates_emitted = len(claim_updates)

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

        try:
            from core_memory.runtime.session.goal_lifecycle import resolve_goals_for_turn
            goal_visible_ids = sorted(
                set(visible_ids + [str(x) for x in (req.get("window_bead_ids") or []) if str(x).strip()])
            )
            goal_lifecycle = resolve_goals_for_turn(
                root=root,
                session_id=req["session_id"],
                turn_id=req["turn_id"],
                outcome_bead_id=canonical_turn_bead_id or bead_id,
                visible_bead_ids=goal_visible_ids,
            )
        except Exception:
            goal_lifecycle = {"ok": False, "error": "goal_lifecycle_failed"}

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
            "association_pass": auto_apply,
            "preview_association_queued": preview_queued,
            "merge": turn_merge,
            "decision_pass": decision_pass,
        },
        "claim_telemetry": claim_telemetry,
        "claim_updates_emitted": claim_updates_emitted,
        "memory_outcome_written": memory_outcome_written,
        "goal_lifecycle": goal_lifecycle,
        "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
    }
    return out
