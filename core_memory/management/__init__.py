"""Governed maintenance facade for agent control-plane operations.

The public ``maintain()`` verb intentionally routes through existing Core
Memory operations instead of exposing raw file/index mutation to agents.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAINTAIN_CONTRACT = "core_memory.maintain.v1"
ASSOCIATION_CANDIDATE_ACTIONS = {
    "accept",
    "approve",
    "approved",
    "associate",
    "link",
    "linked",
    "supported",
    "reject",
    "reject_candidate",
    "modify",
    "invert",
    "replace",
    "add",
    "create",
    "create_edge",
    "add_link",
    "no_link",
    "no link",
    "no-link",
    "no_supported_link",
    "no_supported_links",
    "none",
    "not_supported",
    "unsupported",
}


@dataclass(frozen=True)
class ActionPolicy:
    required_authority: tuple[str, ...] = ()
    actor_required: bool = False
    mutating: bool = False
    idempotency_required: bool = False
    event_hook_allowed: bool = False
    apply_description: str = ""


ACTION_POLICIES: dict[str, ActionPolicy] = {
    "request_memory_approval": ActionPolicy(("request_memory_approval", "user_confirmed", "admin_repair"), True, True),
    "approve_memory": ActionPolicy(("approve_memory", "user_confirmed", "admin_repair"), True, True),
    "reject_memory": ActionPolicy(("reject_memory", "user_confirmed", "admin_repair"), True, True),
    "confirm_memory": ActionPolicy(("confirm_memory", "user_confirmed", "admin_repair"), True, True),
    "flush_session": ActionPolicy(("session_maintenance", "user_confirmed", "admin_repair"), True, True),
    "session_start": ActionPolicy(("session_maintenance", "user_confirmed", "admin_repair"), True, True),
    "enqueue_job": ActionPolicy(("queue_ops", "admin_repair"), True, True),
    "run_jobs": ActionPolicy(("queue_ops", "admin_repair"), True, True),
    "association_run": ActionPolicy(("run_association_judge", "queue_ops", "admin_repair"), True, True),
    "decide_association_candidate": ActionPolicy(
        ("append_judged_association", "user_confirmed", "admin_repair"), True, True
    ),
    "apply_association_proposals": ActionPolicy(("append_judged_association", "admin_repair"), True, True),
    "decide_dreamer_candidate": ActionPolicy(
        ("decide_dreamer_candidate", "user_confirmed", "admin_repair"), True, True
    ),
    "submit_entity_merge_proposal": ActionPolicy(("submit_entity_merge_proposal", "admin_repair"), True, True),
    "apply_reviewed_proposal": ActionPolicy(("apply_reviewed_proposal", "user_confirmed", "admin_repair"), True, True),
    "refresh_myelination": ActionPolicy(("refresh_myelination", "queue_ops", "admin_repair"), True, True),
    "propose_soul_update": ActionPolicy(("propose_soul_update", "admin_repair"), True, True),
    "approve_soul_update": ActionPolicy(("approve_soul_update", "user_confirmed", "admin_repair"), True, True),
    "reject_soul_update": ActionPolicy(("reject_soul_update", "user_confirmed", "admin_repair"), True, True),
    "correct_memory": ActionPolicy(("correct_memory", "user_confirmed", "admin_repair"), True, True),
    "mark_outdated": ActionPolicy(("mark_outdated", "user_confirmed", "admin_repair"), True, True),
    "supersede_memory": ActionPolicy(("supersede_memory", "user_confirmed", "admin_repair"), True, True),
    "deactivate_association": ActionPolicy(("deactivate_association", "user_confirmed", "admin_repair"), True, True),
    "request_re_review": ActionPolicy(("run_association_judge", "queue_ops", "admin_repair"), True, True),
    "remove_beads": ActionPolicy(("remove_bead", "user_confirmed", "admin_repair"), True, True),
    "tombstone_bead": ActionPolicy(
        ("remove_bead", "user_confirmed", "admin_repair"),
        actor_required=True,
        mutating=True,
        idempotency_required=True,
    ),
    "reauthor_memory": ActionPolicy(
        ("reauthor_memory", "admin_repair"),
        actor_required=True,
        mutating=True,
        idempotency_required=True,
    ),
    "retry_pending_semantic": ActionPolicy(
        ("retry_pending_semantic", "admin_repair"),
        actor_required=True,
        mutating=True,
        idempotency_required=True,
    ),
    "remove_source": ActionPolicy(("remove_source", "event_hook", "admin_repair"), True, True, False, True),
}

READ_ACTIONS = {
    "inspect_state",
    "inspect_bead",
    "list_pending_approvals",
    "list_dreamer_candidates",
    "association_coverage_summary",
    "list_association_candidates",
    "myelination_status",
    "inspect_soul",
    "soul_history",
    "semantic_backfill_report",
}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_action(action: str) -> str:
    a = _clean_str(action).lower().replace("-", "_")
    aliases = {
        "delete_bead": "remove_beads",
        "delete_beads": "remove_beads",
        "remove_bead": "remove_beads",
        "prune_bead": "remove_beads",
        "prune_beads": "remove_beads",
        "tombstone": "tombstone_bead",
        "tombstone_memory": "tombstone_bead",
        "retire_bead": "tombstone_bead",
        "source_removed": "remove_source",
        "remove_source_beads": "remove_source",
        "delete_source": "remove_source",
        "delete_source_beads": "remove_source",
        "inspect": "inspect_state",
        "state": "inspect_state",
        "inspect_memory": "inspect_state",
        "request_approval": "request_memory_approval",
        "approve": "approve_memory",
        "reject": "reject_memory",
        "confirm": "confirm_memory",
        "flush": "flush_session",
        "session_flush": "flush_session",
        "start_session": "session_start",
        "enqueue_async_job": "enqueue_job",
        "run_async_jobs": "run_jobs",
        "run_association_judge": "association_run",
        "run_association_coverage": "association_run",
        "association_summary": "association_coverage_summary",
        "association_coverage": "association_coverage_summary",
        "coverage_summary": "association_coverage_summary",
        "list_association": "list_association_candidates",
        "list_association_candidate": "list_association_candidates",
        "association_candidates": "list_association_candidates",
        "association_candidate": "decide_association_candidate",
        "decide_association": "decide_association_candidate",
        "review_association_candidate": "decide_association_candidate",
        "apply_associations": "apply_association_proposals",
        "list_dreamer": "list_dreamer_candidates",
        "decide_dreamer": "decide_dreamer_candidate",
        "submit_entity_merge": "submit_entity_merge_proposal",
        "decide_proposal": "apply_reviewed_proposal",
        "myelination": "myelination_status",
        "myelination_refresh": "refresh_myelination",
        "soul": "inspect_soul",
        "inspect_soul_file": "inspect_soul",
        "soul_update": "propose_soul_update",
        "approve_soul": "approve_soul_update",
        "reject_soul": "reject_soul_update",
        "outdate_memory": "mark_outdated",
        "mark_memory_outdated": "mark_outdated",
        "retract_association": "deactivate_association",
        "rereview_association": "request_re_review",
        "request_association_re_review": "request_re_review",
        "reauthor": "reauthor_memory",
        "retry_semantic": "retry_pending_semantic",
        "retry_pending_turn": "retry_pending_semantic",
        "backfill_report": "semantic_backfill_report",
        "semantic_maintenance_report": "semantic_backfill_report",
    }
    return aliases.get(a, a)


def _policy_payload(policy: ActionPolicy | None, authority: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_authority": list(policy.required_authority) if policy else [],
        "authority_ok": _authority_ok(policy, authority),
        "actor_required": bool(policy.actor_required) if policy else False,
        "idempotency_required": bool(policy.idempotency_required) if policy else False,
        "event_hook_allowed": bool(policy.event_hook_allowed) if policy else False,
    }


def _allowed_authority(authority: dict[str, Any]) -> set[str]:
    allowed = {str(x).strip() for x in (authority.get("allowed_authority") or []) if str(x).strip()}
    if bool(authority.get("user_confirmed")):
        allowed.add("user_confirmed")
    if _clean_str(authority.get("mode")) == "event_hook":
        allowed.add("event_hook")
    return allowed


def _authority_ok(policy: ActionPolicy | None, authority: dict[str, Any]) -> bool:
    if policy is None or not policy.required_authority:
        return True
    allowed = _allowed_authority(authority)
    if "admin_repair" in allowed:
        return True
    return any(req in allowed for req in policy.required_authority)


def _validation_error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code}


def _first(value: Any) -> str:
    if isinstance(value, list):
        return _clean_str(value[0]) if value else ""
    return _clean_str(value)


def _bead_ids_from_targets(targets: dict[str, Any]) -> list[str]:
    bead_ids = [_clean_str(x) for x in (targets.get("bead_ids") or []) if _clean_str(x)]
    if targets.get("bead_id"):
        bead_ids.append(_clean_str(targets.get("bead_id")))
    return list(dict.fromkeys(bead_ids))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _clean_str(value).lower()
    return text in {"1", "true", "yes", "y", "on"}


def _int_or_default(value: Any, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except Exception:
        return max(minimum, int(default))


def _maintenance_environment(scope: dict[str, Any], targets: dict[str, Any]) -> str:
    return _clean_str(scope.get("environment") or targets.get("environment") or "local")


def _copied_tenant_validation_valid(value: Any, *, action: str) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("ok")
        and value.get("applied")
        and _clean_str(value.get("environment")) == "copied_tenant"
        and _clean_str(value.get("action")) == action
        and _clean_str(value.get("operation_contract")) == "memory.semantic_maintenance_receipt.v1"
    )


def _run_semantic_maintenance(
    action: str,
    *,
    root: str,
    scope: dict[str, Any],
    targets: dict[str, Any],
    decision: dict[str, Any],
    authority: dict[str, Any],
    apply: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    from core_memory.runtime.turn.reauthoring import reauthor_memory, retry_pending_semantic

    common = {
        "root": root,
        "actor": _clean_str(authority.get("actor")),
        "environment": _maintenance_environment(scope, targets),
        "apply": bool(apply),
        "idempotency_key": idempotency_key,
        "association_run_inline": _truthy(targets.get("association_run_inline")),
        "copied_tenant_validation_receipt": dict(decision.get("copied_tenant_validation_receipt") or {}),
    }
    if action == "reauthor_memory":
        out = reauthor_memory(
            bead_ids=_bead_ids_from_targets(targets),
            sweep=_truthy(targets.get("sweep")),
            limit=_int_or_default(targets.get("limit"), 50),
            include_v1=_truthy(targets.get("include_v1")),
            thin_only=(True if "thin_only" not in targets else _truthy(targets.get("thin_only"))),
            revision_type=_clean_str(decision.get("revision_type") or targets.get("revision_type")),
            **common,
        )
    else:
        out = retry_pending_semantic(
            session_id=_clean_str(scope.get("session_id") or targets.get("session_id")),
            turn_id=_clean_str(scope.get("turn_id") or targets.get("turn_id")),
            sweep=_truthy(targets.get("sweep")),
            limit=_int_or_default(targets.get("limit"), 50),
            **common,
        )
    out["operation_contract"] = out.get("contract")
    return out


def _association_source_ingest_envelope(targets: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    for row in (
        targets.get("source_ingest_envelope"),
        proposal.get("source_ingest_envelope"),
        targets.get("source_ingest_envelope_ref"),
        proposal.get("source_ingest_envelope_ref"),
    ):
        if isinstance(row, dict):
            return dict(row)
    return {}


def _association_source_ingest_envelope_refs(targets: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for value in (
        targets.get("source_ingest_envelope_refs"),
        proposal.get("source_ingest_envelope_refs"),
    ):
        if isinstance(value, list):
            refs.extend(dict(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            refs.append(dict(value))
    return refs


def _source_payload(targets: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    source = targets.get("source") if isinstance(targets.get("source"), dict) else proposal.get("source")
    return dict(source or {})


def _merge_association_provenance(
    associations: list[dict[str, Any]],
    *,
    targets: dict[str, Any],
    proposal: dict[str, Any],
    decision: dict[str, Any],
    authority: dict[str, Any],
) -> list[dict[str, Any]]:
    defaults: dict[str, Any] = {}
    for key in (
        "judge_model",
        "prompt_version",
        "rubric_version",
        "grounding_hash",
        "truth_basis",
        "provenance",
        "reason_code",
    ):
        value = proposal.get(key)
        if value in (None, "", [], {}):
            value = decision.get(key)
        if value in (None, "", [], {}):
            value = targets.get(key)
        if value not in (None, "", [], {}):
            defaults[key] = value
    if authority.get("actor") and "reviewer" not in defaults:
        defaults["reviewer"] = _clean_str(authority.get("actor"))
    merged: list[dict[str, Any]] = []
    for row in associations:
        if not isinstance(row, dict):
            merged.append(row)
            continue
        out = dict(defaults)
        out.update(row)
        merged.append(out)
    return merged


def _validate_judged_associations(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not rows:
        return [_validation_error("proposal.associations", "associations_required")]
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(_validation_error(f"proposal.associations[{i}]", "association_object_required"))
            continue
        for key in ("judge_model", "prompt_version", "rubric_version"):
            if not _clean_str(row.get(key)):
                errors.append(_validation_error(f"proposal.associations[{i}].{key}", "judge_provenance_required"))
        if not _clean_str(row.get("truth_basis")):
            errors.append(_validation_error(f"proposal.associations[{i}].truth_basis", "truth_basis_required"))
    return errors


def _validate_action(
    action: str,
    *,
    scope: dict[str, Any],
    targets: dict[str, Any],
    proposal: dict[str, Any],
    decision: dict[str, Any],
    authority: dict[str, Any],
    idempotency_key: str,
    apply_requested: bool = False,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    policy = ACTION_POLICIES.get(action)
    if policy and policy.actor_required and not _clean_str(authority.get("actor")):
        errors.append(_validation_error("authority.actor", "actor_required"))
    if policy and policy.idempotency_required and apply_requested and not _clean_str(idempotency_key):
        errors.append(_validation_error("idempotency_key", "idempotency_key_required_for_apply"))

    if action == "remove_beads":
        if not _bead_ids_from_targets(targets):
            errors.append(_validation_error("targets.bead_ids", "bead_ids_required"))
        if not _clean_str(decision.get("reason") or proposal.get("reason") or targets.get("reason")):
            errors.append(_validation_error("decision.reason", "reason_required"))
    elif action == "tombstone_bead":
        bead_ids = _bead_ids_from_targets(targets)
        if not bead_ids:
            errors.append(_validation_error("targets.bead_id", "bead_id_required"))
        elif len(bead_ids) > 1:
            # tombstone_bead is the single-bead semantic action; bulk removal must
            # go through remove_beads so the host opts into multi-bead intent.
            errors.append(_validation_error("targets.bead_id", "single_bead_only_use_remove_beads"))
        if not _clean_str(decision.get("reason") or proposal.get("reason") or targets.get("reason")):
            errors.append(_validation_error("decision.reason", "reason_required"))
    elif action == "remove_source":
        if not _source_payload(targets, proposal):
            errors.append(_validation_error("targets.source", "source_required"))
    elif action in {"request_memory_approval", "approve_memory", "reject_memory", "confirm_memory"}:
        if not _clean_str(targets.get("bead_id") or _first(targets.get("bead_ids"))):
            errors.append(_validation_error("targets.bead_id", "bead_id_required"))
        if action == "reject_memory" and not _clean_str(decision.get("reason")):
            errors.append(_validation_error("decision.reason", "reason_required"))
    elif action in {"flush_session", "session_start"}:
        if not _clean_str(scope.get("session_id") or targets.get("session_id")):
            errors.append(_validation_error("scope.session_id", "session_id_required"))
    elif action == "enqueue_job":
        if not _clean_str(targets.get("kind")):
            errors.append(_validation_error("targets.kind", "job_kind_required"))
    elif action == "association_run":
        if (
            not _bead_ids_from_targets(targets)
            and not _clean_str(scope.get("session_id") or targets.get("session_id"))
            and not _truthy(targets.get("sweep"))
        ):
            errors.append(_validation_error("targets.bead_ids", "bead_ids_session_id_or_sweep_required"))
    elif action == "decide_association_candidate":
        if not _clean_str(targets.get("candidate_id") or decision.get("candidate_id")):
            errors.append(_validation_error("targets.candidate_id", "candidate_id_required"))
        if (
            _clean_str(
                decision.get("action") or decision.get("decision") or targets.get("action") or targets.get("decision")
            ).lower()
            not in ASSOCIATION_CANDIDATE_ACTIONS
        ):
            errors.append(_validation_error("decision.action", "association_candidate_action_required"))
    elif action == "apply_association_proposals":
        rows = _merge_association_provenance(
            list(proposal.get("associations") or []),
            targets=targets,
            proposal=proposal,
            decision=decision,
            authority=authority,
        )
        errors.extend(_validate_judged_associations(rows))
    elif action == "decide_dreamer_candidate":
        if not _clean_str(targets.get("candidate_id")):
            errors.append(_validation_error("targets.candidate_id", "candidate_id_required"))
        if _clean_str(decision.get("decision") or targets.get("decision")).lower() not in {"accept", "reject"}:
            errors.append(_validation_error("decision.decision", "accept_or_reject_required"))
    elif action == "submit_entity_merge_proposal":
        if not _clean_str(proposal.get("source_entity_id")):
            errors.append(_validation_error("proposal.source_entity_id", "source_entity_id_required"))
        if not _clean_str(proposal.get("target_entity_id")):
            errors.append(_validation_error("proposal.target_entity_id", "target_entity_id_required"))
    elif action == "apply_reviewed_proposal":
        if not _clean_str(decision.get("candidate_id") or targets.get("candidate_id")):
            errors.append(_validation_error("decision.candidate_id", "candidate_id_required"))
        if not _clean_str(decision.get("decision") or targets.get("decision")):
            errors.append(_validation_error("decision.decision", "decision_required"))
    elif action == "refresh_myelination":
        pass
    elif action == "propose_soul_update":
        if not _clean_str(proposal.get("target_file") or targets.get("target_file")):
            errors.append(_validation_error("proposal.target_file", "target_file_required"))
        if not _clean_str(proposal.get("entry_key") or targets.get("entry_key")):
            errors.append(_validation_error("proposal.entry_key", "entry_key_required"))
        if _clean_str(proposal.get("op") or "upsert").lower() != "remove" and not _clean_str(proposal.get("content")):
            errors.append(_validation_error("proposal.content", "content_required"))
    elif action in {"approve_soul_update", "reject_soul_update"}:
        if not _clean_str(targets.get("revision_id") or decision.get("revision_id")):
            errors.append(_validation_error("targets.revision_id", "revision_id_required"))
    elif action == "correct_memory":
        if not _clean_str(targets.get("bead_id") or _first(targets.get("bead_ids"))):
            errors.append(_validation_error("targets.bead_id", "bead_id_required"))
        if not _clean_str(proposal.get("correction") or proposal.get("content") or decision.get("correction")):
            errors.append(_validation_error("proposal.correction", "correction_required"))
    elif action == "mark_outdated":
        if not _clean_str(targets.get("bead_id") or _first(targets.get("bead_ids"))):
            errors.append(_validation_error("targets.bead_id", "bead_id_required"))
        if not _clean_str(decision.get("reason") or proposal.get("reason")):
            errors.append(_validation_error("decision.reason", "reason_required"))
    elif action == "supersede_memory":
        if not _clean_str(targets.get("bead_id")):
            errors.append(_validation_error("targets.bead_id", "bead_id_required"))
        if not _clean_str(targets.get("successor_bead_id") or decision.get("successor_bead_id")):
            errors.append(_validation_error("targets.successor_bead_id", "successor_bead_id_required"))
    elif action == "deactivate_association":
        has_id = bool(_clean_str(targets.get("association_id")))
        has_edge = bool(
            _clean_str(targets.get("source_bead") or targets.get("source_bead_id"))
            and _clean_str(targets.get("target_bead") or targets.get("target_bead_id"))
            and _clean_str(targets.get("relationship"))
        )
        if not has_id and not has_edge:
            errors.append(_validation_error("targets.association_id", "association_id_or_edge_required"))
        if not _clean_str(decision.get("reason") or proposal.get("reason")):
            errors.append(_validation_error("decision.reason", "reason_required"))
    elif action == "request_re_review":
        if (
            not _bead_ids_from_targets(targets)
            and not _clean_str(targets.get("association_id"))
            and not _clean_str(scope.get("session_id") or targets.get("session_id"))
        ):
            errors.append(_validation_error("targets", "bead_ids_association_id_or_session_id_required"))
    elif action == "reauthor_memory":
        if not _bead_ids_from_targets(targets) and not _truthy(targets.get("sweep")):
            errors.append(_validation_error("targets", "bead_ids_or_sweep_required"))
        revision_type = _clean_str(decision.get("revision_type") or targets.get("revision_type"))
        if revision_type and revision_type not in {"correction", "reversal"}:
            errors.append(_validation_error("decision.revision_type", "correction_or_reversal_required"))
    elif action == "retry_pending_semantic":
        session_id = _clean_str(scope.get("session_id") or targets.get("session_id"))
        turn_id = _clean_str(scope.get("turn_id") or targets.get("turn_id"))
        if not _truthy(targets.get("sweep")) and not (session_id and turn_id):
            errors.append(_validation_error("targets", "session_and_turn_or_sweep_required"))

    if action in {"reauthor_memory", "retry_pending_semantic"}:
        environment = _clean_str(scope.get("environment") or targets.get("environment") or "local")
        configured_environment = _clean_str(os.environ.get("CORE_MEMORY_MAINTENANCE_ENVIRONMENT"))
        if environment not in {"local", "copied_tenant", "live_tenant"}:
            errors.append(_validation_error("scope.environment", "maintenance_environment_invalid"))
        if configured_environment and configured_environment not in {"local", "copied_tenant", "live_tenant"}:
            errors.append(
                _validation_error(
                    "CORE_MEMORY_MAINTENANCE_ENVIRONMENT",
                    "configured_maintenance_environment_invalid",
                )
            )
        elif environment in {"copied_tenant", "live_tenant"} and not configured_environment:
            errors.append(
                _validation_error(
                    "CORE_MEMORY_MAINTENANCE_ENVIRONMENT",
                    "configured_maintenance_environment_required",
                )
            )
        elif configured_environment and configured_environment != environment:
            errors.append(
                _validation_error(
                    "scope.environment",
                    "maintenance_environment_does_not_match_configured_store",
                )
            )
        if (
            apply_requested
            and environment == "live_tenant"
            and not _copied_tenant_validation_valid(decision.get("copied_tenant_validation_receipt"), action=action)
        ):
            errors.append(
                _validation_error(
                    "decision.copied_tenant_validation_receipt",
                    "copied_tenant_validation_required_before_live_apply",
                )
            )
    return errors


def _preview(
    action: str,
    *,
    root: str,
    scope: dict[str, Any],
    targets: dict[str, Any],
    proposal: dict[str, Any],
    decision: dict[str, Any],
    authority: dict[str, Any],
    validation_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    policy = ACTION_POLICIES.get(action)
    errors = list(validation_errors or [])
    return {
        "ok": not errors,
        "contract": MAINTAIN_CONTRACT,
        "action": action,
        "status": "preview" if not errors else "validation_failed",
        "applied": False,
        "dry_run": True,
        "root": root,
        "scope": scope,
        "targets": targets,
        "proposal": proposal,
        "decision": decision,
        "authority": authority,
        "validation_errors": errors,
        **_policy_payload(policy, authority),
    }


def _authority_denied(action: str, *, authority: dict[str, Any]) -> dict[str, Any]:
    policy = ACTION_POLICIES.get(action)
    return {
        "ok": False,
        "contract": MAINTAIN_CONTRACT,
        "action": action,
        "status": "authority_denied",
        "error": "maintain_authority_required",
        **_policy_payload(policy, authority),
    }


def _validation_failed(action: str, *, authority: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    policy = ACTION_POLICIES.get(action)
    return {
        "ok": False,
        "contract": MAINTAIN_CONTRACT,
        "action": action,
        "status": "validation_failed",
        "error": "maintain_validation_failed",
        "validation_errors": list(errors),
        **_policy_payload(policy, authority),
    }


def _augment(
    out: dict[str, Any],
    *,
    action: str,
    authority: dict[str, Any],
    validation_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    policy = ACTION_POLICIES.get(action)
    result = dict(out)
    result["contract"] = MAINTAIN_CONTRACT
    result["action"] = action
    result.setdefault("validation_errors", list(validation_errors or []))
    result.update(_policy_payload(policy, authority))
    return result


def _myelination_status(root: str) -> dict[str, Any]:
    from core_memory.runtime.observability.myelination import read_myelination_manifest

    manifest_path = Path(root) / ".beads" / "events" / "myelination-manifest.json"
    manifest = read_myelination_manifest(root)
    return {
        "ok": bool(manifest.get("ok", True)),
        "contract": MAINTAIN_CONTRACT,
        "action": "myelination_status",
        "manifest_path": str(manifest_path),
        "manifest_present": bool(manifest.get("present")),
        "manifest": manifest,
    }


def _resolve_rereview_beads(root: str, targets: dict[str, Any]) -> list[str]:
    bead_ids = _bead_ids_from_targets(targets)
    assoc_id = _clean_str(targets.get("association_id"))
    if not assoc_id:
        return bead_ids
    try:
        index = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return bead_ids
    candidates = list(index.get("associations") or [])
    retracted = index.get("retracted_associations") or {}
    if isinstance(retracted, dict):
        candidates.extend([x for x in retracted.values() if isinstance(x, dict)])
    for assoc in candidates:
        if not isinstance(assoc, dict) or _clean_str(assoc.get("id")) != assoc_id:
            continue
        for key in ("source_bead", "target_bead"):
            val = _clean_str(assoc.get(key))
            if val and val not in bead_ids:
                bead_ids.append(val)
    return list(dict.fromkeys(bead_ids))


def remove_beads(
    *,
    root: str = ".",
    bead_ids: list[str],
    reason: str,
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    source: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    from core_memory.persistence.store import MemoryStore

    return MemoryStore(root=root).remove_beads(
        list(bead_ids or []),
        reason=reason,
        actor=actor,
        authority=dict(authority or {}),
        dry_run=bool(dry_run),
        apply=bool(apply),
        source=dict(source or {}),
        idempotency_key=idempotency_key,
    )


def remove_bead(
    *,
    root: str = ".",
    bead_id: str,
    reason: str,
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    return remove_beads(
        root=root,
        bead_ids=[bead_id],
        reason=reason,
        actor=actor,
        authority=authority,
        dry_run=dry_run,
        apply=apply,
        idempotency_key=idempotency_key,
    )


def _tombstone_bead_id(targets: dict[str, Any]) -> str:
    ids = _bead_ids_from_targets(targets)
    return ids[0] if ids else ""


def _tombstone_receipt(
    out: dict[str, Any],
    *,
    reviewer: str = "",
    notes: str = "",
    tombstone_type: str = "",
) -> dict[str, Any]:
    """Annotate a remove_beads receipt as a semantic single-bead tombstone.

    The durable record stays the existing ``bead_removed`` event (reason, actor,
    idempotency key, bead snapshot); this only surfaces the semantic mapping and
    the optional review fields so hosts get a clear receipt.
    """
    result = dict(out)
    result["tombstone_event"] = "bead_removed"
    if reviewer:
        result["reviewer"] = reviewer
    if notes:
        result["notes"] = notes
    if tombstone_type:
        result["tombstone_type"] = tombstone_type
    return result


def tombstone_bead(
    *,
    root: str = ".",
    bead_id: str,
    reason: str,
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
    reviewer: str = "",
    notes: str = "",
    tombstone_type: str = "",
) -> dict[str, Any]:
    """Governed single-bead tombstone: a semantic wrapper over ``remove_beads``.

    Removes one mistaken bead from active recall/graph truth while preserving
    audit history. Not a new deletion path — it delegates to the existing
    ``remove_beads`` machinery (tombstone event, association pruning, index
    metadata, semantic/trace dirty marking, graph/sync retraction, idempotency).
    """
    out = remove_beads(
        root=root,
        bead_ids=[bead_id],
        reason=reason,
        actor=actor,
        authority=authority,
        dry_run=dry_run,
        apply=apply,
        idempotency_key=idempotency_key,
    )
    return _tombstone_receipt(out, reviewer=reviewer, notes=notes, tombstone_type=tombstone_type)


def remove_source(
    *,
    root: str = ".",
    source: dict[str, Any],
    reason: str = "source removed",
    actor: str = "",
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    from core_memory.persistence.store import MemoryStore

    return MemoryStore(root=root).remove_source(
        dict(source or {}),
        reason=reason,
        actor=actor,
        authority=dict(authority or {}),
        dry_run=bool(dry_run),
        apply=bool(apply),
        idempotency_key=idempotency_key,
        limit=max(1, int(limit)),
    )


def maintain(
    *,
    root: str = ".",
    action: str,
    scope: dict[str, Any] | None = None,
    targets: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    action_n = _normalize_action(action)
    scope_d = dict(scope or {})
    targets_d = dict(targets or {})
    proposal_d = dict(proposal or {})
    decision_d = dict(decision or {})
    authority_d = dict(authority or {})
    root_final = _clean_str(root or scope_d.get("root")) or "."
    apply_requested = bool(apply) and not bool(dry_run)

    if not action_n:
        return {"ok": False, "contract": MAINTAIN_CONTRACT, "error": "maintain_action_required"}

    if action_n == "inspect_state":
        from core_memory.integrations.api import inspect_state

        out = inspect_state(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id")) or None,
            as_of=_clean_str(scope_d.get("as_of")) or None,
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "inspect_bead":
        from core_memory.integrations.api import inspect_bead

        bead_id = _clean_str(targets_d.get("bead_id") or _first(targets_d.get("bead_ids")))
        bead = inspect_bead(root=root_final, bead_id=bead_id)
        if bead is None:
            return {
                "ok": False,
                "contract": MAINTAIN_CONTRACT,
                "action": action_n,
                "error": "bead_not_found",
                "bead_id": bead_id,
            }
        return {"ok": True, "contract": MAINTAIN_CONTRACT, "action": action_n, "bead": bead}

    if action_n == "list_pending_approvals":
        from core_memory import list_pending_approvals

        out = dict(list_pending_approvals(root=root_final, limit=int(targets_d.get("limit") or 100)))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "list_dreamer_candidates":
        from core_memory.runtime.dreamer.candidates import list_dreamer_candidates

        out = list_dreamer_candidates(
            root=root_final,
            status=_clean_str(targets_d.get("status")) or None,
            limit=int(targets_d.get("limit") or 100),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "association_coverage_summary":
        from core_memory.runtime.associations.coverage import association_coverage_summary

        out = association_coverage_summary(root=root_final, limit=_int_or_default(targets_d.get("limit"), 10))
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "list_association_candidates":
        from core_memory.runtime.associations.coverage import list_association_candidates

        out = list_association_candidates(
            root=root_final,
            status=_clean_str(targets_d.get("status") or scope_d.get("status")) or None,
            limit=_int_or_default(targets_d.get("limit"), 100),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "myelination_status":
        return _myelination_status(root_final)

    if action_n == "inspect_soul":
        subject = _clean_str(targets_d.get("subject") or scope_d.get("soul_subject")) or "self"
        file_name = _clean_str(targets_d.get("file_name") or targets_d.get("target_file"))
        if file_name:
            from core_memory.soul.store import read_soul_file

            out = read_soul_file(root_final, file_name=file_name, subject=subject)
        else:
            from core_memory.soul.store import list_soul_files

            out = list_soul_files(root_final, subject=subject)
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "soul_history":
        from core_memory.soul.store import soul_history

        out = soul_history(
            root_final,
            subject=_clean_str(targets_d.get("subject") or scope_d.get("soul_subject")) or "self",
            limit=int(targets_d.get("limit") or 500),
        )
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        return out

    if action_n == "semantic_backfill_report":
        from core_memory.runtime.turn.reauthoring import semantic_backfill_report

        out = semantic_backfill_report(root_final)
        out["operation_contract"] = out.get("contract")
        out["contract"] = MAINTAIN_CONTRACT
        out["action"] = action_n
        out["ok"] = True
        return out

    policy = ACTION_POLICIES.get(action_n)
    validation_errors = _validate_action(
        action_n,
        scope=scope_d,
        targets=targets_d,
        proposal=proposal_d,
        decision=decision_d,
        authority=authority_d,
        idempotency_key=idempotency_key,
        apply_requested=apply_requested,
    )
    if policy and not apply_requested:
        if not validation_errors and action_n in {"reauthor_memory", "retry_pending_semantic"}:
            out = _run_semantic_maintenance(
                action_n,
                root=root_final,
                scope=scope_d,
                targets=targets_d,
                decision=decision_d,
                authority=authority_d,
                apply=False,
                idempotency_key=idempotency_key,
            )
            return _augment(
                out,
                action=action_n,
                authority=authority_d,
                validation_errors=validation_errors,
            )
        if not validation_errors and action_n == "remove_beads":
            out = remove_beads(
                root=root_final,
                bead_ids=_bead_ids_from_targets(targets_d),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason")),
                actor=_clean_str(authority_d.get("actor")),
                authority=authority_d,
                dry_run=True,
                apply=False,
                idempotency_key=idempotency_key,
            )
            return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)
        if not validation_errors and action_n == "remove_source":
            out = remove_source(
                root=root_final,
                source=_source_payload(targets_d, proposal_d),
                reason=_clean_str(
                    decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason") or "source removed"
                ),
                actor=_clean_str(authority_d.get("actor")),
                authority=authority_d,
                dry_run=True,
                apply=False,
                idempotency_key=idempotency_key,
                limit=int(targets_d.get("limit") or 1000),
            )
            return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)
        if not validation_errors and action_n == "tombstone_bead":
            out = tombstone_bead(
                root=root_final,
                bead_id=_tombstone_bead_id(targets_d),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason")),
                actor=_clean_str(authority_d.get("actor")),
                authority=authority_d,
                dry_run=True,
                apply=False,
                idempotency_key=idempotency_key,
                reviewer=_clean_str(decision_d.get("reviewer")),
                notes=_clean_str(decision_d.get("notes")),
                tombstone_type=_clean_str(decision_d.get("tombstone_type")),
            )
            return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)
        return _preview(
            action_n,
            root=root_final,
            scope=scope_d,
            targets=targets_d,
            proposal=proposal_d,
            decision=decision_d,
            authority=authority_d,
            validation_errors=validation_errors,
        )
    if validation_errors:
        return _validation_failed(action_n, authority=authority_d, errors=validation_errors)
    if policy and not _authority_ok(policy, authority_d):
        return _authority_denied(action_n, authority=authority_d)

    if action_n in {"reauthor_memory", "retry_pending_semantic"}:
        out = _run_semantic_maintenance(
            action_n,
            root=root_final,
            scope=scope_d,
            targets=targets_d,
            decision=decision_d,
            authority=authority_d,
            apply=True,
            idempotency_key=idempotency_key,
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "remove_beads":
        out = remove_beads(
            root=root_final,
            bead_ids=_bead_ids_from_targets(targets_d),
            reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason")),
            actor=_clean_str(authority_d.get("actor")),
            authority=authority_d,
            dry_run=bool(dry_run),
            apply=bool(apply),
            idempotency_key=idempotency_key,
        )
        return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)

    if action_n == "tombstone_bead":
        out = tombstone_bead(
            root=root_final,
            bead_id=_tombstone_bead_id(targets_d),
            reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason")),
            actor=_clean_str(authority_d.get("actor")),
            authority=authority_d,
            dry_run=bool(dry_run),
            apply=bool(apply),
            idempotency_key=idempotency_key,
            reviewer=_clean_str(decision_d.get("reviewer")),
            notes=_clean_str(decision_d.get("notes")),
            tombstone_type=_clean_str(decision_d.get("tombstone_type")),
        )
        return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)

    if action_n == "remove_source":
        out = remove_source(
            root=root_final,
            source=_source_payload(targets_d, proposal_d),
            reason=_clean_str(
                decision_d.get("reason") or proposal_d.get("reason") or targets_d.get("reason") or "source removed"
            ),
            actor=_clean_str(authority_d.get("actor")),
            authority=authority_d,
            dry_run=bool(dry_run),
            apply=bool(apply),
            idempotency_key=idempotency_key,
            limit=int(targets_d.get("limit") or 1000),
        )
        return _augment(out, action=action_n, authority=authority_d, validation_errors=validation_errors)

    if action_n == "request_memory_approval":
        from core_memory import request_approval

        return _augment(
            request_approval(
                root=root_final,
                bead_id=_clean_str(targets_d.get("bead_id")),
                requested_by=_clean_str(authority_d.get("actor")),
                note=_clean_str(decision_d.get("note")),
            ),
            action=action_n,
            authority=authority_d,
        )

    if action_n == "approve_memory":
        from core_memory import approve_bead

        return _augment(
            approve_bead(
                root=root_final,
                bead_id=_clean_str(targets_d.get("bead_id")),
                approver=_clean_str(authority_d.get("actor")),
                note=_clean_str(decision_d.get("note")),
            ),
            action=action_n,
            authority=authority_d,
        )

    if action_n == "reject_memory":
        from core_memory import reject_bead

        return _augment(
            reject_bead(
                root=root_final,
                bead_id=_clean_str(targets_d.get("bead_id")),
                approver=_clean_str(authority_d.get("actor")),
                reason=_clean_str(decision_d.get("reason")),
            ),
            action=action_n,
            authority=authority_d,
        )

    if action_n == "confirm_memory":
        from core_memory import confirm_bead

        return _augment(
            confirm_bead(
                root=root_final, bead_id=_clean_str(targets_d.get("bead_id")), note=_clean_str(decision_d.get("note"))
            ),
            action=action_n,
            authority=authority_d,
        )

    if action_n == "flush_session":
        from core_memory.runtime.engine import process_flush

        out = process_flush(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id") or "default"),
            source=_clean_str(authority_d.get("actor") or "maintain"),
            flush_tx_id=_clean_str(idempotency_key),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "session_start":
        from core_memory.runtime.engine import process_session_start

        out = process_session_start(
            root=root_final,
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id") or "default"),
            source=_clean_str(authority_d.get("actor") or "maintain"),
            max_items=int(targets_d.get("max_items") or 80),
            soul_subject=_clean_str(targets_d.get("soul_subject") or scope_d.get("soul_subject")) or "self",
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "enqueue_job":
        from core_memory.runtime.queue.jobs import enqueue_async_job

        out = enqueue_async_job(
            root=root_final,
            kind=_clean_str(targets_d.get("kind")),
            event=dict(proposal_d.get("event") or {}),
            ctx=dict(scope_d.get("ctx") or {}),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "run_jobs":
        from core_memory.runtime.queue.jobs import run_async_jobs

        out = run_async_jobs(
            root=root_final,
            run_semantic=bool(targets_d.get("run_semantic", True)),
            max_compaction=int(targets_d.get("max_compaction") or 1),
            max_side_effects=int(targets_d.get("max_side_effects") or 2),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "association_run":
        from core_memory.runtime.associations.coverage import enqueue_association_coverage

        out = enqueue_association_coverage(
            root=root_final,
            bead_ids=_bead_ids_from_targets(targets_d),
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id")) or None,
            trigger=_clean_str(targets_d.get("trigger") or proposal_d.get("trigger") or "operator"),
            candidate_bead_ids=list(targets_d.get("candidate_bead_ids") or []),
            run_inline=_truthy(targets_d.get("run_inline")),
            max_candidates=_int_or_default(targets_d.get("max_candidates"), 40),
            graph_revision=_clean_str(targets_d.get("graph_revision") or proposal_d.get("graph_revision")),
            prompt_version=_clean_str(
                targets_d.get("prompt_version") or proposal_d.get("prompt_version") or "association_judge.v2"
            ),
            rubric_version=_clean_str(
                targets_d.get("rubric_version") or proposal_d.get("rubric_version") or "association_truth.v2"
            ),
            sweep=_truthy(targets_d.get("sweep")),
            sweep_mode=_clean_str(targets_d.get("sweep_mode") or proposal_d.get("sweep_mode") or "incomplete"),
            sweep_cursor=_clean_str(targets_d.get("sweep_cursor") or proposal_d.get("sweep_cursor")),
            sweep_limit=_int_or_default(
                targets_d.get("sweep_limit"), _int_or_default(targets_d.get("max_candidates"), 40)
            ),
            source_ingest_envelope=_association_source_ingest_envelope(targets_d, proposal_d),
            source_ingest_envelope_refs=_association_source_ingest_envelope_refs(targets_d, proposal_d),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "decide_association_candidate":
        from core_memory.runtime.associations.coverage import decide_association_candidate

        out = decide_association_candidate(
            root=root_final,
            candidate_id=_clean_str(targets_d.get("candidate_id") or decision_d.get("candidate_id")),
            action=_clean_str(
                decision_d.get("action")
                or decision_d.get("decision")
                or targets_d.get("action")
                or targets_d.get("decision")
            ),
            run_id=_clean_str(targets_d.get("run_id") or decision_d.get("run_id")),
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id")) or None,
            reviewer=_clean_str(authority_d.get("actor") or decision_d.get("reviewer")),
            reason_text=_clean_str(
                decision_d.get("reason_text") or decision_d.get("reason") or proposal_d.get("reason")
            ),
            truth_basis=_clean_str(decision_d.get("truth_basis") or proposal_d.get("truth_basis")),
            confidence=decision_d.get("confidence"),
            relationship=_clean_str(decision_d.get("relationship") or targets_d.get("relationship")),
            direction=_clean_str(decision_d.get("direction") or targets_d.get("direction")),
            source_bead=_clean_str(decision_d.get("source_bead") or targets_d.get("source_bead")),
            target_bead=_clean_str(decision_d.get("target_bead") or targets_d.get("target_bead")),
            evidence_refs=list(decision_d.get("evidence_refs") or proposal_d.get("evidence_refs") or []),
            evidence_bead_ids=list(decision_d.get("evidence_bead_ids") or proposal_d.get("evidence_bead_ids") or []),
            judge_model=_clean_str(decision_d.get("judge_model") or proposal_d.get("judge_model")),
            prompt_version=_clean_str(
                decision_d.get("prompt_version") or proposal_d.get("prompt_version") or "association_judge.v2"
            ),
            rubric_version=_clean_str(
                decision_d.get("rubric_version") or proposal_d.get("rubric_version") or "association_truth.v2"
            ),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "apply_association_proposals":
        from core_memory.runtime.associations.coverage import apply_association_proposals

        rows = _merge_association_provenance(
            list(proposal_d.get("associations") or []),
            targets=targets_d,
            proposal=proposal_d,
            decision=decision_d,
            authority=authority_d,
        )
        out = apply_association_proposals(
            root=root_final,
            associations=rows,
            run_id=_clean_str(targets_d.get("run_id") or proposal_d.get("run_id")),
            session_id=_clean_str(scope_d.get("session_id")) or None,
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "decide_dreamer_candidate":
        from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate

        out = decide_dreamer_candidate(
            root=root_final,
            candidate_id=_clean_str(targets_d.get("candidate_id")),
            decision=_clean_str(decision_d.get("decision") or targets_d.get("decision")),
            reviewer=_clean_str(authority_d.get("actor")),
            notes=_clean_str(decision_d.get("notes")),
            apply=True,
            resolution=_clean_str(decision_d.get("resolution") or targets_d.get("resolution")) or None,
            scope_a=_clean_str(decision_d.get("scope_a") or targets_d.get("scope_a")) or None,
            scope_b=_clean_str(decision_d.get("scope_b") or targets_d.get("scope_b")) or None,
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "submit_entity_merge_proposal":
        from core_memory.integrations.mcp.typed_write import submit_entity_merge_proposal

        out = submit_entity_merge_proposal(root=root_final, **proposal_d)
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "apply_reviewed_proposal":
        from core_memory.integrations.mcp.typed_write import apply_reviewed_proposal

        payload = dict(decision_d)
        payload.setdefault("candidate_id", _clean_str(targets_d.get("candidate_id")))
        payload.setdefault("decision", _clean_str(targets_d.get("decision")))
        out = apply_reviewed_proposal(root=root_final, apply=True, **payload)
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "refresh_myelination":
        payload = dict(proposal_d.get("event") or {})
        payload.update({k: v for k, v in targets_d.items() if k in {"since", "limit", "idempotency_key"}})
        payload.setdefault("idempotency_key", _clean_str(idempotency_key) or "maintain:refresh_myelination")
        if bool(targets_d.get("run_inline")):
            from core_memory.runtime.queue.side_effect_queue import process_side_effect_event

            out = process_side_effect_event(root=root_final, kind="myelination-update", payload=payload)
        else:
            from core_memory.runtime.queue.jobs import enqueue_async_job

            out = enqueue_async_job(root=root_final, kind="myelination-update", event=payload, ctx={})
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "propose_soul_update":
        from core_memory.soul.store import propose_soul_update

        out = propose_soul_update(
            root_final,
            target_file=_clean_str(proposal_d.get("target_file") or targets_d.get("target_file")),
            entry_key=_clean_str(proposal_d.get("entry_key") or targets_d.get("entry_key")),
            content=_clean_str(proposal_d.get("content")),
            op=_clean_str(proposal_d.get("op") or "upsert"),
            subject=_clean_str(proposal_d.get("subject") or targets_d.get("subject") or scope_d.get("soul_subject"))
            or "self",
            source=_clean_str(proposal_d.get("source") or "agent"),
            epistemic_status=_clean_str(proposal_d.get("epistemic_status") or "inferred"),
            reason=_clean_str(proposal_d.get("reason") or decision_d.get("reason")),
            evidence=[dict(x) for x in (proposal_d.get("evidence") or []) if isinstance(x, dict)],
            requires_approval=bool(proposal_d.get("requires_approval", True)),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "approve_soul_update":
        from core_memory.soul.store import approve_soul_update

        out = approve_soul_update(
            root_final,
            revision_id=_clean_str(targets_d.get("revision_id") or decision_d.get("revision_id")),
            subject=_clean_str(targets_d.get("subject") or scope_d.get("soul_subject")) or "self",
            approver=_clean_str(authority_d.get("actor")),
            note=_clean_str(decision_d.get("note")),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "reject_soul_update":
        from core_memory.soul.store import reject_soul_update

        out = reject_soul_update(
            root_final,
            revision_id=_clean_str(targets_d.get("revision_id") or decision_d.get("revision_id")),
            subject=_clean_str(targets_d.get("subject") or scope_d.get("soul_subject")) or "self",
            reviewer=_clean_str(authority_d.get("actor")),
            reason=_clean_str(decision_d.get("reason") or decision_d.get("note")),
        )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n in {"correct_memory", "mark_outdated", "supersede_memory", "deactivate_association"}:
        from core_memory.persistence.store import MemoryStore
        from core_memory.persistence.store_management_ops import (
            correct_memory_for_store,
            deactivate_association_for_store,
            mark_outdated_memory_for_store,
            supersede_memory_for_store,
        )

        store = MemoryStore(root=root_final)
        if action_n == "correct_memory":
            out = correct_memory_for_store(
                store,
                bead_id=_clean_str(targets_d.get("bead_id") or _first(targets_d.get("bead_ids"))),
                correction=_clean_str(
                    proposal_d.get("correction") or proposal_d.get("content") or decision_d.get("correction")
                ),
                actor=_clean_str(authority_d.get("actor")),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason")),
                title=_clean_str(proposal_d.get("title")),
                archive_target=bool(decision_d.get("archive_target", authority_d.get("user_confirmed", False))),
                idempotency_key=idempotency_key,
            )
        elif action_n == "mark_outdated":
            out = mark_outdated_memory_for_store(
                store,
                bead_id=_clean_str(targets_d.get("bead_id") or _first(targets_d.get("bead_ids"))),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason")),
                actor=_clean_str(authority_d.get("actor")),
                idempotency_key=idempotency_key,
            )
        elif action_n == "supersede_memory":
            out = supersede_memory_for_store(
                store,
                bead_id=_clean_str(targets_d.get("bead_id")),
                successor_bead_id=_clean_str(targets_d.get("successor_bead_id") or decision_d.get("successor_bead_id")),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason")),
                actor=_clean_str(authority_d.get("actor")),
                idempotency_key=idempotency_key,
            )
        else:
            out = deactivate_association_for_store(
                store,
                association_id=_clean_str(targets_d.get("association_id")),
                source_bead=_clean_str(targets_d.get("source_bead") or targets_d.get("source_bead_id")),
                target_bead=_clean_str(targets_d.get("target_bead") or targets_d.get("target_bead_id")),
                relationship=_clean_str(targets_d.get("relationship")),
                reason=_clean_str(decision_d.get("reason") or proposal_d.get("reason")),
                actor=_clean_str(authority_d.get("actor")),
                idempotency_key=idempotency_key,
            )
        return _augment(out, action=action_n, authority=authority_d)

    if action_n == "request_re_review":
        from core_memory.runtime.associations.coverage import enqueue_association_coverage

        out = enqueue_association_coverage(
            root=root_final,
            bead_ids=_resolve_rereview_beads(root_final, targets_d),
            session_id=_clean_str(scope_d.get("session_id") or targets_d.get("session_id")) or None,
            trigger=_clean_str(targets_d.get("trigger") or "operator_rereview"),
            candidate_bead_ids=list(targets_d.get("candidate_bead_ids") or []),
            run_inline=bool(targets_d.get("run_inline")),
            max_candidates=int(targets_d.get("max_candidates") or 40),
        )
        return _augment(out, action=action_n, authority=authority_d)

    return {
        "ok": False,
        "contract": MAINTAIN_CONTRACT,
        "action": action_n,
        "error": "unsupported_maintain_action",
    }


__all__ = [
    "ACTION_POLICIES",
    "MAINTAIN_CONTRACT",
    "maintain",
    "remove_bead",
    "remove_beads",
    "remove_source",
]
