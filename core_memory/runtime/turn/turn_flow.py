from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from core_memory.schema.agent_authored_updates import AgentAuthoredUpdatesV1, AuthoringMode


def flag_structural_coverage_missing(root: str, bead_id: str) -> bool:
    """Mark a persisted bead as lacking required non-temporal associations (F-W2).

    Returns True when the flag landed on an existing bead.
    """
    if not str(bead_id or "").strip():
        return False
    from core_memory.persistence.store import MemoryStore

    store = MemoryStore(root)
    idx = store._read_json(store.beads_dir / "index.json")
    bead = (idx.get("beads") or {}).get(str(bead_id))
    if not bead:
        return False
    bead["structural_coverage_missing"] = True
    store._write_json(store.beads_dir / "index.json", idx)
    return True


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
    crawler_updates: AgentAuthoredUpdatesV1 | None,
    authoring_mode: AuthoringMode | None,
    metadata: dict[str, Any] | None,
    policy: Any,
    normalize_turn_request: Callable[..., dict[str, Any]],
    mark_turn_checkpoint: Callable[..., Any],
    maybe_emit_finalize_memory_event: Callable[..., dict[str, Any]],
    build_crawler_context: Callable[..., dict[str, Any]],
    invoke_turn_crawler_agent: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    resolve_reviewed_updates: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    repair_enabled: bool,
    repair_turn_memory_fn: Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]],
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
        crawler_updates=crawler_updates,
        authoring_mode=authoring_mode,
        metadata=metadata,
    )

    # Never-forget: contract enforcement happens after the canonical turn event
    # is written. A blocked semantic gate therefore preserves the raw turn as a
    # retryable pending record without fabricating a canonical context bead.
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
        crawler_updates=req["crawler_updates"],
        authoring_mode=req["authoring_mode"],
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

    # Build the crawler context once: the canonical turn bead is created later
    # by the association pass (apply_crawler_updates), so everything from here
    # to that pass operates on the same pre-write session surface.
    crawler_ctx = build_crawler_context(root=root, session_id=req["session_id"], limit=200)
    invoked_updates, invocation_diag = invoke_turn_crawler_agent(
        root=root,
        req=req,
        crawler_context=crawler_ctx,
    )

    req_for_updates = dict(req)
    source_override = None
    if isinstance(req.get("crawler_updates"), dict) and req.get("crawler_updates"):
        source_override = str(req.get("_crawler_updates_source") or "crawler_updates")
    elif isinstance(invoked_updates, dict) and invoked_updates:
        req_for_updates["crawler_updates"] = dict(invoked_updates)
        source_override = str(invocation_diag.get("source") or "agent_callable")
    if isinstance(invocation_diag.get("authorship"), dict):
        req_for_updates["authorship_provenance"] = dict(invocation_diag["authorship"])

    reviewed_updates, gate = resolve_reviewed_updates(
        req_for_updates,
        source_override=source_override,
        invocation_diag=invocation_diag,
        max_create_per_turn=getattr(policy, "max_create_per_turn", None),
    )

    if gate.get("blocked") and repair_enabled:
        invalid_updates = req_for_updates.get("crawler_updates")
        repair_updates, repair_diag = repair_turn_memory_fn(
            root=root,
            req=req_for_updates,
            crawler_context=crawler_ctx,
            invalid_updates=dict(invalid_updates) if isinstance(invalid_updates, dict) else None,
            validation={
                "error_code": str(gate.get("error_code") or error_agent_updates_missing),
                "details": dict(gate.get("validation") or {}),
            },
        )
        gate["repair_attempt"] = dict(repair_diag or {})
        if isinstance(repair_updates, dict) and repair_updates:
            repair_req = dict(req_for_updates)
            repair_req["crawler_updates"] = dict(repair_updates)
            if isinstance(repair_diag.get("authorship"), dict):
                repair_req["authorship_provenance"] = dict(repair_diag["authorship"])
            repaired_updates, repaired_gate = resolve_reviewed_updates(
                repair_req,
                source_override="repair_agent",
                invocation_diag=repair_diag,
                max_create_per_turn=getattr(policy, "max_create_per_turn", None),
            )
            if not repaired_gate.get("blocked") and isinstance(repaired_updates, dict):
                repaired_gate["repair_attempt"] = dict(repair_diag)
                repaired_gate["warnings"] = [
                    *list(gate.get("warnings") or []),
                    {
                        "code": "primary_authorship_repaired",
                        "error_code": str(gate.get("error_code") or error_agent_updates_missing),
                        "details": dict(gate.get("validation") or {}),
                    },
                    *list(repaired_gate.get("warnings") or []),
                ]
                reviewed_updates = repaired_updates
                gate = repaired_gate
            else:
                gate["repair_validation"] = {
                    "error_code": str(repaired_gate.get("error_code") or "agent_updates_invalid"),
                    "details": dict(repaired_gate.get("validation") or {}),
                }

    if gate.get("blocked"):
        emit_agent_turn_quality_metric(
            root=root,
            req=req,
            gate=gate,
            updates=reviewed_updates,
            result="blocked",
            error_code=str(gate.get("error_code") or error_agent_updates_missing),
        )
        contract_error = {
            "code": str(gate.get("error_code") or error_agent_updates_missing),
            "details": dict(gate.get("validation") or {}),
        }
        # The raw event is the never-forget source. Hard mode must not make a
        # deterministic context bead look like committed semantic authorship.
        return {
            "ok": False,
            "mode": "turn",
            "authority_path": "canonical_in_process",
            "processed": 1,
            "failed": 1,
            "bead_id": "",
            "gate_blocked": True,
            "delta": delta,
            "emitted": emitted,
            "enrichment_queued": False,
            "enrichment_queue": {},
            "crawler_handoff": {
                "required": True,
                "agent_authored_gate": gate,
                "association_pass": {},
                "preview_association_queued": 0,
                "merge": {},
                "decision_pass": {},
            },
            "error_code": contract_error["code"],
            "error": "agent-authored updates are pending semantic repair",
            "agent_contract_error": contract_error,
            "engine": {
                "normalized": True,
                "entry": "process_turn_finalized",
                "sequence_owner": "memory_engine",
            },
        }
    else:
        contract_error = None

    _structural_coverage_missing = False
    if not isinstance(reviewed_updates, dict):
        reviewed_updates = default_crawler_updates(req)
    reviewed_updates = ensure_turn_creation_update(
        root,
        req,
        reviewed_updates,
        strict_contract=bool(gate.get("required"))
        or str(reviewed_updates.get("schema_version") or "") == "agent_authored_updates.v1",
    )

    # F-W2: semantic-coverage gate — violations always flag, never block
    # (never-forget); the flag is written after the association pass creates
    # the canonical bead.
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
            # Never-forget: semantic coverage violations always use warn/flag mode —
            # never block with no bead written. The flag lands on the bead (F-W2)
            # and the agent can backfill associations. Hard-block mode is removed
            # because amnesia is worse than a coverage gap.
            _structural_coverage_missing = True
            gate["warned"] = True
            if not bool(gate.get("fail_open")):
                gate["error_code"] = gate.get("error_code") or error_agent_semantic_coverage_missing
            logger.warning(
                "agent-authored gate: structural coverage missing, "
                "session=%s turn=%s semantic_count=%d min_required=%d",
                req.get("session_id"),
                req.get("turn_id"),
                semantic_count,
                min_required,
            )

    # F-W2: the coverage flag is written after the association pass — the
    # canonical turn bead does not exist yet at this point (it is created by
    # apply_crawler_updates, not process_memory_event), so flagging from
    # `delta` here was dead code that never landed on any bead.

    # F-W1: enqueue semantic-write and enrichment stages instead of running
    # them inline. The canonical current-turn bead does not exist until the
    # queued association stage succeeds.
    from core_memory.runtime.passes.enrichment import _enrichment_queue_enabled, enqueue_turn_enrichment

    # process_memory_event is mechanical-only and never returns a bead_id; the
    # canonical turn bead is created by the association pass (inline below, or
    # inside the queued enrichment job — in which case the engine fills the id
    # in after draining).
    bead_id = ""
    enrichment_queued = False
    enrichment_queue: dict[str, Any] = {}

    if _enrichment_queue_enabled():
        enqueue_result = enqueue_turn_enrichment(
            root=root,
            session_id=req["session_id"],
            turn_id=req["turn_id"],
            bead_id=bead_id,
            req=req,
            reviewed_updates=reviewed_updates,
            crawler_ctx=crawler_ctx,
            authorship=dict(gate.get("authorship") or {}),
            structural_coverage_missing=_structural_coverage_missing,
        )
        enrichment_queue = dict(enqueue_result or {})
        enrichment_queued = bool(enqueue_result and enqueue_result.get("ok"))

    auto_apply: dict[str, Any] = {}
    preview_queued = 0
    turn_merge: dict[str, Any] = {}
    decision_pass: dict[str, Any] = {}
    claim_telemetry: dict[str, Any] = {}
    claim_updates_emitted = 0
    memory_outcome_written = False
    memory_outcome_advisory: dict[str, Any] | None = None
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

        bead_id = str(auto_apply.get("current_turn_bead_id") or "")
        if not bead_id:
            created_ids = [str(x) for x in (auto_apply.get("created_bead_ids") or []) if str(x)]
            bead_id = created_ids[0] if created_ids else ""
        if not bead_id:
            from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id

            bead_id = str(
                find_canonical_turn_bead_id(
                    root, session_id=req["session_id"], turn_id=req["turn_id"], preferred_bead_ids=[]
                )
                or ""
            )

        # F-W2: flag the canonical bead now that it exists.
        if _structural_coverage_missing and bead_id:
            flag_structural_coverage_missing(root, bead_id)

        session_visible_after = session_visible_bead_ids(root=root, session_id=req["session_id"])
        visible_ids = sorted(set(crawler_visible + session_visible_after))

        if extract_and_attach_claims_fn is not None:
            created_bead_ids = list(auto_apply.get("created_bead_ids") or [])
            claim_req = dict(req)
            claim_req["authorship"] = dict(gate.get("authorship") or {})
            claim_req["authored_updates"] = dict(reviewed_updates or {})
            claim_telemetry = (
                extract_and_attach_claims_fn(
                    root,
                    req["session_id"],
                    req["turn_id"],
                    created_bead_ids,
                    claim_req,
                )
                or {}
            )

        preview_queued = queue_preview_associations(
            root=root, session_id=req["session_id"], visible_bead_ids=visible_ids
        )
        turn_merge = merge_crawler_updates(root=root, session_id=req["session_id"])

        decision_pass = run_session_decision_pass(
            root=root,
            session_id=req["session_id"],
            visible_bead_ids=visible_ids,
            turn_id=req["turn_id"],
            updates=reviewed_updates,
            authorship=dict(gate.get("authorship") or {}),
        )

        canonical_turn_bead_id = str(claim_telemetry.get("canonical_bead_id") or "")
        claims_batch = list(claim_telemetry.get("claims_batch") or [])
        if emit_claim_updates_fn is not None and canonical_turn_bead_id and claims_batch:
            claim_visible_ids = sorted(
                set(visible_ids + [str(x) for x in (req.get("window_bead_ids") or []) if str(x).strip()])
            )
            claim_updates = (
                emit_claim_updates_fn(
                    root,
                    claims_batch,
                    canonical_turn_bead_id,
                    session_id=req["session_id"],
                    visible_bead_ids=claim_visible_ids,
                    reviewed_updates=reviewed_updates,
                    decision_pass=decision_pass,
                    authorship=dict(gate.get("authorship") or {}),
                )
                or []
            )
            claim_updates_emitted = len(claim_updates)

            if classify_memory_outcome_fn is not None and canonical_turn_bead_id:
                md = dict(req.get("metadata") or {})
                context_beads = list(
                    md.get("retrieved_beads") or md.get("context_beads") or req.get("window_bead_ids") or []
                )
                turn_context = {
                    "retrieved_beads": context_beads,
                    "query": str(req.get("user_query") or ""),
                    "used_memory": bool(md.get("used_memory")) or bool(context_beads),
                    "correction_triggered": bool(md.get("correction_triggered") or md.get("memory_correction")),
                    "reflection_triggered": bool(md.get("reflection_triggered") or md.get("memory_reflection")),
                }
                outcome = classify_memory_outcome_fn(turn_context)
                if isinstance(outcome, dict):
                    # Deterministic memory-use classification is useful telemetry,
                    # but it cannot mutate the canonical turn bead.
                    memory_outcome_advisory = dict(outcome)

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
            root=root,
            req=req,
            gate=gate,
            updates=reviewed_updates,
            result="success",
        )

    _gate_blocked = False
    out: dict[str, Any] = {
        "ok": not _gate_blocked,
        "mode": "turn",
        "authority_path": "canonical_in_process",
        "processed": 1,
        "failed": int(_gate_blocked),
        "bead_id": bead_id,
        "gate_blocked": _gate_blocked,
        "delta": delta,
        "emitted": emitted,
        "enrichment_queued": enrichment_queued,
        "enrichment_queue": enrichment_queue,
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
        "memory_outcome_advisory": memory_outcome_advisory,
        "goal_lifecycle": goal_lifecycle,
        "engine": {"normalized": True, "entry": "process_turn_finalized", "sequence_owner": "memory_engine"},
    }
    return out
