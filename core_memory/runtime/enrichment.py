"""Turn enrichment: post-persist stages that run outside the critical path.

F-W1: These stages were moved from the synchronous turn pipeline to the
side effect queue. A queued-stage failure never fails the turn — the bead
is already persisted.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _enrichment_queue_enabled() -> bool:
    return os.environ.get("CORE_MEMORY_ENRICHMENT_QUEUE", "on").strip().lower() != "off"


def enqueue_turn_enrichment(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    bead_id: str,
    req: dict[str, Any],
    reviewed_updates: dict[str, Any] | None = None,
    crawler_ctx: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Enqueue post-persist enrichment stages for a turn.

    Returns the enqueue result, or None if the queue is disabled.
    """
    if not _enrichment_queue_enabled():
        return None

    from core_memory.runtime.side_effect_queue import enqueue_side_effect_event
    from core_memory.runtime.session_enrichment_delta import crawler_updates_to_delta

    idempotency_key = f"enrich-{session_id}-{turn_id}"
    enrichment_delta = crawler_updates_to_delta(
        session_id=session_id,
        turn_id=turn_id,
        updates=reviewed_updates or {},
        crawler_ctx=crawler_ctx or {},
        source_kind="queued",
        authority_path="turn-enrichment",
        origin=str(req.get("origin") or "USER_TURN"),
        idempotency_key=idempotency_key,
        window_turn_ids=list(req.get("window_turn_ids") or []),
    )

    return enqueue_side_effect_event(
        root=root,
        kind="turn-enrichment",
        payload={
            "session_id": session_id,
            "turn_id": turn_id,
            "bead_id": bead_id,
            "user_query": str(req.get("user_query") or ""),
            "assistant_final": str(req.get("assistant_final") or ""),
            "reviewed_updates": dict(reviewed_updates or {}),
            "enrichment_delta": enrichment_delta,
            "crawler_visible_bead_ids": list((crawler_ctx or {}).get("visible_bead_ids") or []),
            "metadata": dict(req.get("metadata") or {}),
            "window_bead_ids": list(req.get("window_bead_ids") or []),
        },
        idempotency_key=idempotency_key,
    )


def run_turn_enrichment(*, root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute post-persist enrichment stages for a turn.

    Stages: association pass, claim extraction, preview associations,
    crawler merge, decision pass, claim updates, memory outcome, quality metric.

    Each stage is run independently — a failure in one does not block others.
    """
    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    bead_id = str(payload.get("bead_id") or "")
    reviewed_updates = dict(payload.get("reviewed_updates") or {})
    if not reviewed_updates and isinstance(payload.get("enrichment_delta"), dict):
        from core_memory.runtime.session_enrichment_delta import delta_to_crawler_updates
        reviewed_updates = delta_to_crawler_updates(dict(payload.get("enrichment_delta") or {}))
    crawler_visible = list(payload.get("crawler_visible_bead_ids") or [])

    from core_memory.runtime.engine import (
        _session_visible_bead_ids,
        _emit_agent_turn_quality_metric,
        _queue_preview_associations,
    )
    from core_memory.runtime.association_pass import run_association_pass
    from core_memory.association.crawler_contract import merge_crawler_updates
    from core_memory.runtime.decision_pass import run_session_decision_pass
    from core_memory.integrations.openclaw_flags import (
        claim_layer_enabled,
    )

    results: dict[str, Any] = {"stages_completed": [], "stages_failed": []}

    session_visible = _session_visible_bead_ids(root=root, session_id=session_id)
    visible_ids = sorted(set(crawler_visible + session_visible))

    # Stage 1: association pass
    try:
        auto_apply = run_association_pass(
            root=root,
            session_id=session_id,
            updates=reviewed_updates,
            visible_bead_ids=visible_ids,
        )
        results["stages_completed"].append("association")
        results["association"] = {
            "ok": True,
            "created": len(auto_apply.get("created_bead_ids") or []),
            "created_bead_ids": list(auto_apply.get("created_bead_ids") or []),
            "current_turn_bead_id": str(auto_apply.get("current_turn_bead_id") or ""),
        }
    except Exception as exc:
        logger.warning("enrichment: association pass failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("association")

    # Refresh visible IDs after association pass
    session_visible = _session_visible_bead_ids(root=root, session_id=session_id)
    visible_ids = sorted(set(crawler_visible + session_visible))

    # Stage 2: claim extraction
    claim_telemetry: dict[str, Any] = {}
    if claim_layer_enabled():
        try:
            from core_memory.runtime.engine import extract_and_attach_claims
            created_bead_ids = list((results.get("association") or {}).get("created_bead_ids") or [])
            req_stub = {"session_id": session_id, "turn_id": turn_id, "user_query": payload.get("user_query", "")}
            claim_telemetry = extract_and_attach_claims(root, session_id, turn_id, created_bead_ids, req_stub) or {}
            results["stages_completed"].append("claims")
        except Exception as exc:
            logger.warning("enrichment: claim extraction failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("claims")

    # Stage 3: preview associations
    try:
        preview_queued = _queue_preview_associations(root=root, session_id=session_id, visible_bead_ids=visible_ids)
        results["stages_completed"].append("preview_assoc")
    except Exception as exc:
        logger.warning("enrichment: preview associations failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("preview_assoc")
        preview_queued = 0

    # Stage 4: crawler merge
    try:
        turn_merge = merge_crawler_updates(root=root, session_id=session_id)
        results["stages_completed"].append("crawler_merge")
    except Exception as exc:
        logger.warning("enrichment: crawler merge failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("crawler_merge")
        turn_merge = {}

    # Stage 5: decision pass
    try:
        decision_pass = run_session_decision_pass(
            root=root,
            session_id=session_id,
            visible_bead_ids=visible_ids,
            turn_id=turn_id,
        )
        results["stages_completed"].append("decision_pass")
    except Exception as exc:
        logger.warning("enrichment: decision pass failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("decision_pass")
        decision_pass = {}

    # Stage 6: claim updates
    if claim_layer_enabled():
        try:
            from core_memory.runtime.engine import emit_claim_updates
            canonical_bead_id = str(claim_telemetry.get("canonical_bead_id") or bead_id)
            claims_batch = list(claim_telemetry.get("claims_batch") or [])
            if canonical_bead_id and claims_batch:
                emit_claim_updates(
                    root, claims_batch, canonical_bead_id,
                    session_id=session_id, visible_bead_ids=visible_ids,
                    reviewed_updates=reviewed_updates, decision_pass=decision_pass,
                )
            results["stages_completed"].append("claim_updates")
        except Exception as exc:
            logger.warning("enrichment: claim updates failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("claim_updates")

    # Stage 7: memory outcome
    if claim_layer_enabled():
        try:
            from core_memory.runtime.engine import classify_memory_outcome, write_memory_outcome_to_bead
            canonical_bead_id = str(claim_telemetry.get("canonical_bead_id") or bead_id)
            if canonical_bead_id:
                md = dict(payload.get("metadata") or {})
                context_beads = list(
                    md.get("retrieved_beads")
                    or md.get("context_beads")
                    or payload.get("window_bead_ids")
                    or []
                )
                turn_context = {
                    "retrieved_beads": context_beads,
                    "query": str(payload.get("user_query") or ""),
                    "used_memory": bool(md.get("used_memory")) or bool(context_beads),
                    "correction_triggered": bool(md.get("correction_triggered") or md.get("memory_correction")),
                    "reflection_triggered": bool(md.get("reflection_triggered") or md.get("memory_reflection")),
                }
                outcome = classify_memory_outcome(turn_context)
                if isinstance(outcome, dict):
                    write_memory_outcome_to_bead(
                        root, canonical_bead_id,
                        interaction_role=outcome.get("interaction_role"),
                        memory_outcome=outcome.get("memory_outcome"),
                    )
            results["stages_completed"].append("memory_outcome")
        except Exception as exc:
            logger.warning("enrichment: memory outcome failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("memory_outcome")

    # Stage 8: quality metric
    try:
        _emit_agent_turn_quality_metric(
            root=root,
            req={"session_id": session_id, "turn_id": turn_id},
            gate={},
            updates=reviewed_updates,
            result="enrichment_complete",
            preview_association_queued=int(preview_queued),
            merge_associations_appended=int(turn_merge.get("associations_appended") or 0),
        )
        results["stages_completed"].append("quality_metric")
    except Exception as exc:
        logger.warning("enrichment: quality metric failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("quality_metric")

    results["ok"] = len(results["stages_failed"]) == 0
    results["turn_id"] = turn_id
    results["bead_id"] = bead_id
    return results
