"""Turn enrichment: post-persist stages that run outside the critical path.

F-W1: These stages were moved from the synchronous turn pipeline to the
side effect queue. A queued-stage failure never fails the turn — the bead
is already persisted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STAGE_RESULTS_DEFAULTS: dict[str, dict[str, Any]] = {
    "association_pass":     {"queued": 0, "skipped_existing": 0},
    "claim_extraction":     {"extracted": 0, "skipped_duplicate": 0},
    "preview_associations": {"queued": 0, "skipped_existing": 0},
    "crawler_merge":        {"merged": 0, "quarantined": 0},
    "decision_pass":        {"emitted": 0, "skipped_grounding": 0},
    "claim_updates":        {"emitted": 0, "skipped_grounding": 0},
    "memory_outcome":       {"written": False},
    "goal_lifecycle":       {"transitioned": 0},
    "quality_metric":       {"score": None},
}


def _enrichment_envelope_path(root: str, bead_id: str, run_id: str) -> Path:
    safe_bead = str(bead_id or "unknown").replace("/", "-").replace("\\", "-")
    return Path(root) / ".beads" / "events" / f"enrichment-{safe_bead}-{run_id[:8]}.jsonl"


def _run_idempotency_token(bead_id: str, run_id: str) -> str:
    raw = (str(bead_id or "") + str(run_id or "")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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

    from core_memory.runtime.queue.side_effect_queue import enqueue_side_effect_event
    from core_memory.runtime.session.session_enrichment_delta import (
        crawler_updates_to_delta,
        delta_to_crawler_updates,
        write_delta_quarantine,
    )

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
    quarantine_result = write_delta_quarantine(root, enrichment_delta)
    projected_reviewed_updates = delta_to_crawler_updates(enrichment_delta)

    return enqueue_side_effect_event(
        root=root,
        kind="turn-enrichment",
        payload={
            "session_id": session_id,
            "turn_id": turn_id,
            "bead_id": bead_id,
            "enrichment_run_id": hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "user_query": str(req.get("user_query") or ""),
            "assistant_final": str(req.get("assistant_final") or ""),
            "reviewed_updates": projected_reviewed_updates,
            "enrichment_delta": enrichment_delta,
            "delta_quarantine": quarantine_result,
            "crawler_visible_bead_ids": list((crawler_ctx or {}).get("visible_bead_ids") or []),
            "metadata": dict(req.get("metadata") or {}),
            "window_bead_ids": list(req.get("window_bead_ids") or []),
        },
        idempotency_key=idempotency_key,
    )


def run_turn_enrichment(
    *,
    root: str,
    payload: dict[str, Any],
    enrichment_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute post-persist enrichment stages for a turn.

    Stages: association pass, claim extraction, preview associations,
    crawler merge, decision pass, claim updates, memory outcome, goal lifecycle,
    quality metric.

    Each stage is run independently — a failure in one does not block others.
    Idempotent when the same `enrichment_run_id` is supplied on repeated calls.
    """
    if not enrichment_run_id:
        enrichment_run_id = uuid.uuid4().hex

    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    bead_id = str(payload.get("bead_id") or "")
    triggered_at = datetime.now(timezone.utc).isoformat()

    # Idempotency gate: return cached result if this run already completed.
    envelope_path = _enrichment_envelope_path(root, bead_id, enrichment_run_id)
    if envelope_path.exists():
        try:
            last_line = [ln for ln in envelope_path.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
            cached = json.loads(last_line)
            return {
                "ok": True,
                "idempotent": True,
                "enrichment_run_id": enrichment_run_id,
                "stage_results": cached.get("stage_results", {}),
                "bead_id": bead_id,
                "turn_id": turn_id,
            }
        except Exception:
            pass  # corrupt envelope — proceed with full run

    enrichment_delta = payload.get("enrichment_delta") if isinstance(payload.get("enrichment_delta"), dict) else None
    reviewed_updates = dict(payload.get("reviewed_updates") or {})
    if isinstance(enrichment_delta, dict):
        from core_memory.runtime.session.session_enrichment_delta import delta_to_crawler_updates
        reviewed_updates = delta_to_crawler_updates(dict(enrichment_delta or {}))
    crawler_visible = list(payload.get("crawler_visible_bead_ids") or [])

    from core_memory.runtime.engine import (
        _session_visible_bead_ids,
        _emit_agent_turn_quality_metric,
        _queue_preview_associations,
    )
    from core_memory.runtime.passes.association_pass import run_association_pass
    from core_memory.association.crawler_contract import merge_crawler_updates
    from core_memory.runtime.passes.decision_pass import run_session_decision_pass
    from core_memory.config.feature_flags import (
        claim_layer_enabled,
    )

    results: dict[str, Any] = {"stages_completed": [], "stages_failed": []}
    stage_results: dict[str, Any] = deepcopy(_STAGE_RESULTS_DEFAULTS)
    if isinstance(enrichment_delta, dict):
        delta_diag = dict(enrichment_delta.get("diagnostics") or {})
        results["enrichment_delta"] = {
            "schema": str(enrichment_delta.get("schema") or ""),
            "idempotency_key": str((enrichment_delta.get("source") or {}).get("idempotency_key") or ""),
            "accepted_counts": dict(delta_diag.get("accepted_counts") or {}),
            "quarantined_counts": dict(delta_diag.get("quarantined_counts") or {}),
            "quarantined": int(delta_diag.get("quarantined") or 0),
        }

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
        results["auto_apply"] = auto_apply
        stage_results["association_pass"]["queued"] = len(auto_apply.get("created_bead_ids") or [])
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
            stage_results["claim_extraction"]["extracted"] = len(claim_telemetry.get("claims_batch") or [])
        except Exception as exc:
            logger.warning("enrichment: claim extraction failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("claims")

    # Stage 3: preview associations
    try:
        preview_queued = _queue_preview_associations(root=root, session_id=session_id, visible_bead_ids=visible_ids)
        results["stages_completed"].append("preview_assoc")
        stage_results["preview_associations"]["queued"] = int(preview_queued or 0)
    except Exception as exc:
        logger.warning("enrichment: preview associations failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("preview_assoc")
        preview_queued = 0

    # Stage 4: crawler merge — atomic (merge write + log clear share the same store_lock inside merge_crawler_updates)
    try:
        turn_merge = merge_crawler_updates(root=root, session_id=session_id)
        results["merge"] = turn_merge
        results["stages_completed"].append("crawler_merge")
        stage_results["crawler_merge"]["merged"] = int(turn_merge.get("associations_appended") or 0)
        stage_results["crawler_merge"]["quarantined"] = int(turn_merge.get("associations_quarantined") or 0)
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
        results["decision_pass"] = decision_pass
        results["stages_completed"].append("decision_pass")
        stage_results["decision_pass"]["emitted"] = int(
            len(list((decision_pass or {}).get("claim_updates") or []))
            + len(list((decision_pass or {}).get("decisions") or []))
        )
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
            emitted_updates: list[dict] = []
            if canonical_bead_id and claims_batch:
                claim_visible_ids = sorted(
                    set(visible_ids + [str(x) for x in (payload.get("window_bead_ids") or []) if str(x).strip()])
                )
                emitted_updates = emit_claim_updates(
                    root, claims_batch, canonical_bead_id,
                    session_id=session_id, visible_bead_ids=claim_visible_ids,
                    reviewed_updates=reviewed_updates, decision_pass=decision_pass,
                ) or []
            results["stages_completed"].append("claim_updates")
            stage_results["claim_updates"]["emitted"] = len(emitted_updates)
        except Exception as exc:
            logger.warning("enrichment: claim updates failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("claim_updates")

    # Stage 7: memory outcome
    if claim_layer_enabled():
        try:
            from core_memory.runtime.engine import classify_memory_outcome, write_memory_outcome_to_bead
            canonical_bead_id = str(claim_telemetry.get("canonical_bead_id") or bead_id)
            memory_outcome_written = False
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
                    memory_outcome_written = True
            results["stages_completed"].append("memory_outcome")
            stage_results["memory_outcome"]["written"] = memory_outcome_written
        except Exception as exc:
            logger.warning("enrichment: memory outcome failed for turn %s: %s", turn_id, exc)
            results["stages_failed"].append("memory_outcome")

    # Stage 8: goal lifecycle resolution
    try:
        from core_memory.runtime.session.goal_lifecycle import resolve_goals_for_turn
        canonical_bead_id = str((claim_telemetry or {}).get("canonical_bead_id") or bead_id)
        goal_visible_ids = sorted(
            set(visible_ids + [str(x) for x in (payload.get("window_bead_ids") or []) if str(x).strip()])
        )
        goal_lifecycle = resolve_goals_for_turn(
            root=root,
            session_id=session_id,
            turn_id=turn_id,
            outcome_bead_id=canonical_bead_id,
            visible_bead_ids=goal_visible_ids,
        )
        results["goal_lifecycle"] = goal_lifecycle
        results["stages_completed"].append("goal_lifecycle")
        stage_results["goal_lifecycle"]["transitioned"] = int(
            (goal_lifecycle or {}).get("transitioned") or (goal_lifecycle or {}).get("resolved") or 0
        )
    except Exception as exc:
        logger.warning("enrichment: goal lifecycle failed for turn %s: %s", turn_id, exc)
        results["stages_failed"].append("goal_lifecycle")

    # Stage 9: quality metric
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

    # Persist delta envelope — idempotency key for future gate checks.
    try:
        envelope = {
            "schema": "session_enrichment_delta.v1",
            "bead_id": bead_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "enrichment_run_id": enrichment_run_id,
            "triggered_at": triggered_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_token": _run_idempotency_token(bead_id, enrichment_run_id),
            "stages_run": list(stage_results.keys()),
            "stage_results": stage_results,
        }
        envelope_path.parent.mkdir(parents=True, exist_ok=True)
        with envelope_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(envelope) + "\n")
    except Exception as exc:
        logger.warning("enrichment: failed to persist delta envelope for turn %s: %s", turn_id, exc)

    results["ok"] = len(results["stages_failed"]) == 0
    results["turn_id"] = turn_id
    results["bead_id"] = bead_id
    results["enrichment_run_id"] = enrichment_run_id
    results["stage_results"] = stage_results
    return results
