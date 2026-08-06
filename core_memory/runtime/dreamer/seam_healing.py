from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from core_memory.persistence.dreamer_candidate_store import (
    candidate_key,
    candidates_path,
    make_candidate_row,
    now_iso,
    read_candidates,
    write_candidates,
)
from core_memory.retrieval.roadmap_planner import STITCHED_PLAN_SCHEMA

SEAM_HEALING_CONTRACT = "core_memory.seam_healing_candidates.v1"
SEAM_HEALING_ORIGIN = "stitch_healed"
SEAM_HEALING_HYPOTHESIS = "seam_healing_candidate"
SEAM_HEALING_RELATIONSHIP_SIGNAL = "stitch_healed"


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _clean_str(value).lower() in {"1", "true", "yes", "y", "accepted", "validated", "pass", "passed"}


def _unwrap_plan(plan_or_receipt: Mapping[str, Any]) -> dict[str, Any]:
    plan = plan_or_receipt.get("plan") if isinstance(plan_or_receipt.get("plan"), Mapping) else plan_or_receipt
    return dict(plan) if isinstance(plan, Mapping) else {}


def _validation_accepted(validation: Mapping[str, Any]) -> bool:
    for key in (
        "validated",
        "accepted",
        "answer_validated",
        "path_validated",
        "seam_validated",
        "outcome_validated",
        "success",
        "ok",
    ):
        if key in validation and _truthy(validation.get(key)):
            return True
    decision = _clean_str(validation.get("decision") or validation.get("label")).lower()
    return decision in {"accept", "accepted", "validate", "validated", "true_positive", "positive"}


def _validation_granularity(validation: Mapping[str, Any]) -> str:
    raw = _clean_str(
        validation.get("validation_granularity")
        or validation.get("granularity")
        or validation.get("scope")
        or validation.get("level")
        or "answer"
    ).lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in {"seam", "seam_level", "crossing", "crossing_level"}:
        return "seam"
    if normalized in {"path", "path_level", "stitched_path", "source_path"}:
        return "path"
    return "answer"


def _validation_confidence(validation: Mapping[str, Any]) -> float:
    for key in ("confidence", "score", "validation_confidence"):
        try:
            return max(0.0, min(1.0, float(validation.get(key))))
        except (TypeError, ValueError):
            continue
    return 1.0


def _candidate_prior(validation: Mapping[str, Any]) -> float:
    granularity = _validation_granularity(validation)
    base = {"seam": 0.65, "path": 0.55, "answer": 0.35}[granularity]
    return round(max(0.05, min(base, base * _validation_confidence(validation))), 4)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _source_path_id(plan: Mapping[str, Any]) -> str:
    explicit = _clean_str(
        plan.get("source_path_id")
        or plan.get("path_id")
        or plan.get("plan_id")
        or plan.get("trace_id")
        or plan.get("request_id")
    )
    if explicit:
        return explicit
    material = {
        "segments": [
            {
                "segment_id": _clean_str(segment.get("segment_id")),
                "state_id": _clean_str(segment.get("state_id")),
                "bead_ids": [_clean_str(value) for value in (segment.get("bead_ids") or []) if _clean_str(value)],
            }
            for segment in (plan.get("segments") or [])
            if isinstance(segment, Mapping)
        ],
        "junctions": [
            {
                "junction_id": _clean_str(junction.get("junction_id")),
                "tier": _clean_str(junction.get("tier")),
                "left_bead_id": _clean_str(junction.get("left_bead_id")),
                "right_bead_id": _clean_str(junction.get("right_bead_id")),
            }
            for junction in (plan.get("junctions") or [])
            if isinstance(junction, Mapping)
        ],
        "terminal_bead_ids": [
            _clean_str(value)
            for value in (plan.get("terminal_bead_ids") or [])
            if _clean_str(value)
        ],
    }
    return f"stitched-path-{_stable_hash(material)}"


def _seam_junctions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for junction in plan.get("junctions") or []:
        if not isinstance(junction, Mapping) or not _truthy(junction.get("is_seam")):
            continue
        left = _clean_str(junction.get("left_bead_id") or junction.get("source_bead_id") or junction.get("source_bead"))
        right = _clean_str(
            junction.get("right_bead_id") or junction.get("target_bead_id") or junction.get("target_bead")
        )
        if not left or not right or left == right:
            continue
        row = dict(junction)
        row["left_bead_id"] = left
        row["right_bead_id"] = right
        rows.append(row)
    return rows


def validate_seam_healing_request(
    *,
    plan: Mapping[str, Any] | None,
    validation: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    plan_d = _unwrap_plan(plan or {})
    validation_d = dict(validation or {})
    if not plan_d:
        errors.append({"field": "proposal.plan", "code": "stitched_plan_required"})
        return errors
    if _clean_str(plan_d.get("schema_version")) != STITCHED_PLAN_SCHEMA:
        errors.append({"field": "proposal.plan.schema_version", "code": "stitched_plan_schema_required"})
    if not _truthy(plan_d.get("stitched")):
        errors.append({"field": "proposal.plan.stitched", "code": "stitched_plan_required"})
    if not _seam_junctions(plan_d):
        errors.append({"field": "proposal.plan.junctions", "code": "seam_junctions_with_endpoints_required"})
    if not validation_d or not _validation_accepted(validation_d):
        errors.append({"field": "decision.validation", "code": "validated_stitched_path_required"})
    return errors


def propose_seam_healing_candidates(
    *,
    root: str | Path,
    plan: Mapping[str, Any],
    validation: Mapping[str, Any],
    reviewer: str = "",
    notes: str = "",
    relationship: str = "associated_with",
    run_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create reviewable seam-healing candidates from a validated stitched plan.

    This function never writes graph associations. It preserves Phase 7's
    guardrail by placing seam crossings in the Dreamer review queue with explicit
    stitched-path provenance and a lower prior for answer-level validation.
    """
    plan_d = _unwrap_plan(plan)
    validation_d = dict(validation or {})
    errors = validate_seam_healing_request(plan=plan_d, validation=validation_d)
    if errors:
        return {
            "ok": False,
            "contract": SEAM_HEALING_CONTRACT,
            "status": "validation_failed",
            "error": "seam_healing_validation_failed",
            "validation_errors": errors,
            "candidate_count": 0,
            "direct_graph_writes": False,
        }

    rows = read_candidates(root)
    existing_by_key = {
        candidate_key(row): row
        for row in rows
        if isinstance(row, dict) and _clean_str(row.get("status")).lower() in {"pending", "accepted"}
    }
    now = now_iso()
    path_id = _source_path_id(plan_d)
    granularity = _validation_granularity(validation_d)
    prior = _candidate_prior(validation_d)
    run_meta = {
        "run_id": _clean_str((run_metadata or {}).get("run_id")) or f"seam-healing-{path_id}",
        "mode": "candidate_review",
        "source": "roadmap_seam_healing",
        "session_id": _clean_str((run_metadata or {}).get("session_id")),
        "flush_tx_id": _clean_str((run_metadata or {}).get("flush_tx_id")),
        "novel_only": True,
        "seen_window_runs": 0,
        "max_exposure": -1,
    }
    candidate_ids: list[str] = []
    deduped_candidate_ids: list[str] = []
    seams = _seam_junctions(plan_d)

    for index, seam in enumerate(seams):
        left = _clean_str(seam.get("left_bead_id"))
        right = _clean_str(seam.get("right_bead_id"))
        junction_id = _clean_str(seam.get("junction_id"))
        tier = _clean_str(seam.get("tier")) or "embedding"
        association = {
            "source": left,
            "target": right,
            "relationship": _clean_str(relationship) or "associated_with",
            "relationship_signal": SEAM_HEALING_RELATIONSHIP_SIGNAL,
            "confidence": prior,
            "grounding": prior,
            "novelty": 0.0,
        }
        rationale = (
            f"Validated stitched roadmap plan crossed seam {junction_id or 'unknown'} "
            f"({tier}) between {left} and {right}; review before promoting a stored association."
        )
        expected = (
            "If accepted through the normal Dreamer review path, the seam can become reviewed association "
            "support for future roadmap planning. This proposal itself performs no graph write."
        )
        review_payload = {
            "kind": "seam_healing_association_candidate",
            "origin": SEAM_HEALING_ORIGIN,
            "source_path_id": path_id,
            "junction_id": junction_id,
            "junction_tier": tier,
            "junction_cost": seam.get("junction_cost"),
            "seam_index": index,
            "validation_granularity": granularity,
            "validation": validation_d,
            "left_bead_id": left,
            "right_bead_id": right,
            "proposed_relationship": association["relationship"],
            "direct_graph_writes": False,
        }
        row = make_candidate_row(
            now=now,
            run_meta=run_meta,
            association=association,
            hypothesis_type=SEAM_HEALING_HYPOTHESIS,
            rationale=rationale,
            expected_decision_impact=expected,
            extras={
                "origin": SEAM_HEALING_ORIGIN,
                "source_path_id": path_id,
                "junction_id": junction_id,
                "junction_tier": tier,
                "seam_index": index,
                "validation_granularity": granularity,
                "submitted_by": _clean_str(reviewer),
                "submission_notes": _clean_str(notes),
                "review_payload": review_payload,
            },
        )
        key = candidate_key(row)
        existing = existing_by_key.get(key)
        if existing is not None:
            deduped_candidate_ids.append(_clean_str(existing.get("id")))
            continue
        rows.append(row)
        existing_by_key[key] = row
        candidate_ids.append(_clean_str(row.get("id")))

    write_candidates(root, rows)
    return {
        "ok": True,
        "contract": SEAM_HEALING_CONTRACT,
        "status": "candidates_submitted",
        "origin": SEAM_HEALING_ORIGIN,
        "source_path_id": path_id,
        "candidate_count": len(candidate_ids),
        "deduped_count": len([cid for cid in deduped_candidate_ids if cid]),
        "candidate_ids": candidate_ids,
        "deduped_candidate_ids": [cid for cid in deduped_candidate_ids if cid],
        "seam_count": len(seams),
        "validation_granularity": granularity,
        "candidate_prior": prior,
        "direct_graph_writes": False,
        "path": str(candidates_path(root)),
    }


__all__ = [
    "SEAM_HEALING_CONTRACT",
    "SEAM_HEALING_HYPOTHESIS",
    "SEAM_HEALING_ORIGIN",
    "propose_seam_healing_candidates",
    "validate_seam_healing_request",
]
