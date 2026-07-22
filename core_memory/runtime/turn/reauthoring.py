"""Governed append-only semantic reauthoring and pending-turn repair.

These maintenance operations deliberately reuse the full delegated
``turn_memory_authoring`` contract and the canonical turn write path.  The
runtime selects sources, validates authority, attaches mechanical provenance,
and schedules post-commit association coverage; it never authors the semantic
interpretation itself.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.association.health import STRUCTURAL_CONTINUITY_RELATIONSHIPS
from core_memory.persistence.bead_hygiene_contract import retrieval_eligibility_downgrade_reasons
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.policy.turn_memory_authoring import author_turn_memory
from core_memory.runtime.passes.agent_authored_contract import validate_agent_authored_updates
from core_memory.runtime.turn.semantic_state import (
    event_for_turn,
    list_semantic_write_states,
    semantic_write_health,
)
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1
from core_memory.schema.normalization import relation_family

SEMANTIC_MAINTENANCE_RECEIPT_V1 = "memory.semantic_maintenance_receipt.v1"
SEMANTIC_BACKFILL_REPORT_V1 = "memory.semantic_backfill_report.v1"
BACKFILL_COHORT_TAG = "agent_led_backfill"
BACKFILL_CONTRACT_TAG = "agent_authored_updates.v1"
_ACTIVE_STATUSES = frozenset({"", "open", "active", "current"})
_UNRESOLVED_STATUSES = frozenset({"pending", "repair_required"})
_EXTERNAL_ANCHOR_TYPES = frozenset(
    {
        "data_insight",
        "document_reference",
        "operational_event",
        "state_assertion",
        "structured_observation",
        "transcript",
    }
)
_SEMANTIC_KEY_FIELDS = (
    "incident_keys",
    "decision_keys",
    "goal_keys",
    "action_keys",
    "outcome_keys",
    "time_keys",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(_clean(value) for value in values if _clean(value)))


def _load_index(root: str | Path) -> dict[str, Any]:
    path = Path(root) / ".beads" / "index.json"
    if not path.exists():
        return {"beads": {}, "associations": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"beads": {}, "associations": []}
    return payload if isinstance(payload, dict) else {"beads": {}, "associations": []}


def _maintenance_audit_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "semantic-maintenance.jsonl"


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _idempotent_replay(
    root: str | Path,
    *,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    path = _maintenance_audit_path(root)
    if not idempotency_key or not path.exists():
        return None
    latest: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _clean(row.get("action")) == action and _clean(row.get("idempotency_key")) == idempotency_key:
            latest = row
    if not latest:
        return None
    if _clean(latest.get("request_fingerprint")) != request_fingerprint:
        return {
            "ok": False,
            "contract": SEMANTIC_MAINTENANCE_RECEIPT_V1,
            "action": action,
            "applied": False,
            "error": "idempotency_key_conflict",
            "idempotency_key": idempotency_key,
        }
    receipt = deepcopy(latest.get("receipt")) if isinstance(latest.get("receipt"), dict) else {}
    receipt["idempotent_replay"] = True
    return receipt


def _append_audit(
    root: str | Path,
    *,
    action: str,
    idempotency_key: str,
    request_fingerprint: str,
    actor: str,
    source_refs: list[str],
    receipt: dict[str, Any],
) -> None:
    row = {
        "schema": SEMANTIC_MAINTENANCE_RECEIPT_V1,
        "audit_id": f"smaint-{uuid.uuid4().hex[:12]}",
        "action": action,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
        "actor": actor,
        "source_refs": list(source_refs),
        "contract_version": AGENT_AUTHORED_UPDATES_V1,
        "recorded_at": _now(),
        "receipt": deepcopy(receipt),
    }
    with store_lock(Path(root)):
        append_jsonl(_maintenance_audit_path(root), row)


def _v1_bead_ids(root: str | Path) -> set[str]:
    ids: set[str] = set()
    for row in list_semantic_write_states(root):
        authorship = row.get("authorship") if isinstance(row.get("authorship"), dict) else {}
        if _clean(authorship.get("schema_version")) != AGENT_AUTHORED_UPDATES_V1:
            continue
        bead_id = _clean(row.get("bead_id"))
        if bead_id:
            ids.add(bead_id)
    # Semantic state names the canonical current-turn bead. V1 companion rows
    # intentionally do not claim the current turn in source_turn_ids, so carry
    # the cohort through explicit derived lineage instead of misclassifying
    # those companions as legacy.
    beads = _load_index(root).get("beads") or {}
    changed = True
    while changed:
        changed = False
        for bead_id, bead in beads.items():
            bead_id_s = _clean(bead_id)
            if bead_id_s in ids or not isinstance(bead, dict):
                continue
            parents = {_clean(value) for value in (bead.get("derived_from_bead_ids") or []) if _clean(value)}
            if parents.intersection(ids):
                ids.add(bead_id_s)
                changed = True
    return ids


def _cohort_for_bead(bead_id: str, bead: dict[str, Any], *, v1_bead_ids: set[str]) -> str:
    tags = {_clean(tag).lower() for tag in (bead.get("tags") or []) if _clean(tag)}
    attribution = bead.get("source_attribution") if isinstance(bead.get("source_attribution"), dict) else {}
    maintenance = (
        attribution.get("core_memory_maintenance")
        if isinstance(attribution.get("core_memory_maintenance"), dict)
        else {}
    )
    if BACKFILL_COHORT_TAG in tags or _clean(maintenance.get("action")) in {
        "reauthor_memory",
        "retry_pending_semantic",
    }:
        return "backfilled"
    if bead_id in v1_bead_ids or BACKFILL_CONTRACT_TAG in tags:
        return "v1_authored"
    return "legacy"


def _source_anchor(bead: dict[str, Any]) -> bool:
    return bool(
        _clean(bead.get("type")) in _EXTERNAL_ANCHOR_TYPES
        or _clean(bead.get("source_id"))
        or _clean(bead.get("source_ref"))
        or bead.get("source_refs")
        or bead.get("hydration_ref")
    )


def _cohort_metrics(
    cohort_ids: set[str],
    beads: dict[str, dict[str, Any]],
    associations: list[dict[str, Any]],
    association_decision_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [beads[bead_id] for bead_id in cohort_ids if bead_id in beads]
    claims_total = sum(len(list(row.get("claims") or [])) for row in rows)
    semantic_keys_total = sum(
        len([value for field in _SEMANTIC_KEY_FIELDS for value in (row.get(field) or []) if _clean(value)])
        for row in rows
    )
    semantic_edges = 0
    causal_edges = 0
    relation_counts: dict[str, int] = {}
    for association in associations:
        status = _clean(association.get("status") or "active").lower()
        if status in {"inactive", "retracted", "superseded"}:
            continue
        source_id = _clean(association.get("source_bead") or association.get("source_bead_id"))
        target_id = _clean(association.get("target_bead") or association.get("target_bead_id"))
        if source_id not in cohort_ids and target_id not in cohort_ids:
            continue
        relationship = _clean(association.get("relationship")).lower()
        if not relationship or relationship in STRUCTURAL_CONTINUITY_RELATIONSHIPS:
            continue
        semantic_edges += 1
        relation_counts[relationship] = int(relation_counts.get(relationship) or 0) + 1
        if relation_family(relationship) in {"causal", "evidence", "influence", "conflict", "revision"}:
            causal_edges += 1
    decision_counts = {
        "decisions": 0,
        "linked": 0,
        "no_link": 0,
        "pending_judge": 0,
        "failed": 0,
    }
    for record in association_decision_rows:
        decisions = [row for row in (record.get("decisions") or []) if isinstance(row, dict)]
        for decision in decisions:
            source_id = _clean(decision.get("source_bead") or decision.get("source_bead_id"))
            target_id = _clean(decision.get("target_bead") or decision.get("target_bead_id"))
            if source_id not in cohort_ids and target_id not in cohort_ids:
                continue
            action = _clean(decision.get("action")).lower()
            decision_counts["decisions"] += 1
            if action in {"no_link", "reject"}:
                decision_counts["no_link"] += 1
            elif action in {"accept", "modify", "invert", "replace", "add"}:
                decision_counts["linked"] += 1
        if decisions:
            continue
        source_ids = {_clean(value) for value in (record.get("source_bead_ids") or []) if _clean(value)}
        no_candidate_ids = {
            _clean(value) for value in (record.get("no_candidate_source_bead_ids") or []) if _clean(value)
        }
        matching_judged = (source_ids - no_candidate_ids).intersection(cohort_ids)
        matching_no_candidate = no_candidate_ids.intersection(cohort_ids)
        if not matching_judged and not matching_no_candidate:
            continue
        status = _clean(record.get("status")).lower()
        if status == "pending_judge":
            decision_counts["pending_judge"] += len(matching_judged)
        elif status in {"judge_failed", "failed", "quarantined"}:
            decision_counts["failed"] += len(matching_judged)
        if matching_no_candidate:
            decision_counts["decisions"] += len(matching_no_candidate)
            decision_counts["no_link"] += len(matching_no_candidate)
        no_supported = int((record.get("counts") or {}).get("no_supported_links") or 0)
        if (no_supported or _clean(record.get("reason")) == "no_candidate_proposals") and not no_candidate_ids:
            matching = source_ids.intersection(cohort_ids)
            decision_counts["decisions"] += len(matching)
            decision_counts["no_link"] += len(matching)
    return {
        "beads": len(rows),
        "source_anchors": sum(1 for row in rows if _source_anchor(row)),
        "retrieval_eligible": sum(1 for row in rows if bool(row.get("retrieval_eligible"))),
        "retrieval_title": sum(1 for row in rows if _clean(row.get("retrieval_title"))),
        "retrieval_facts": sum(1 for row in rows if any(_clean(value) for value in (row.get("retrieval_facts") or []))),
        "retrieval_rich": sum(
            1 for row in rows if not retrieval_eligibility_downgrade_reasons(row, full_contract=True)
        ),
        "beads_with_claims": sum(1 for row in rows if list(row.get("claims") or [])),
        "claims": claims_total,
        "beads_with_semantic_keys": sum(
            1 for row in rows if any(list(row.get(field) or []) for field in _SEMANTIC_KEY_FIELDS)
        ),
        "semantic_keys": semantic_keys_total,
        "semantic_relationships": semantic_edges,
        "causal_edges": causal_edges,
        "relationship_counts": relation_counts,
        "association_decisions": decision_counts,
    }


def semantic_backfill_report(root: str | Path) -> dict[str, Any]:
    """Return cohort-separated richness and causal coverage metrics."""

    index = _load_index(root)
    beads = {
        _clean(bead_id): dict(row)
        for bead_id, row in (index.get("beads") or {}).items()
        if _clean(bead_id) and isinstance(row, dict)
    }
    associations = [dict(row) for row in (index.get("associations") or []) if isinstance(row, dict)]
    decision_path = Path(root) / ".beads" / "events" / "association-judge-decisions.jsonl"
    association_decision_rows: list[dict[str, Any]] = []
    if decision_path.exists():
        for line in decision_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                association_decision_rows.append(row)
    v1_ids = _v1_bead_ids(root)
    cohort_ids: dict[str, set[str]] = {"legacy": set(), "v1_authored": set(), "backfilled": set()}
    for bead_id, bead in beads.items():
        cohort_ids[_cohort_for_bead(bead_id, bead, v1_bead_ids=v1_ids)].add(bead_id)
    return {
        "contract": SEMANTIC_BACKFILL_REPORT_V1,
        "generated_at": _now(),
        "cohorts": {
            cohort: _cohort_metrics(ids, beads, associations, association_decision_rows)
            for cohort, ids in cohort_ids.items()
        },
        "aggregate": _cohort_metrics(set(beads), beads, associations, association_decision_rows),
        "pending_semantic": semantic_write_health(root),
    }


def _is_thin(bead: dict[str, Any]) -> bool:
    status = _clean(bead.get("status")).lower()
    if status not in _ACTIVE_STATUSES:
        return False
    grounding = " ".join(
        [
            _clean(bead.get("title")),
            " ".join(_clean(value) for value in (bead.get("summary") or []) if _clean(value)),
            _clean(bead.get("detail")),
        ]
    ).strip()
    if len(grounding) < 24:
        return False
    return bool(retrieval_eligibility_downgrade_reasons(bead, full_contract=True))


def _select_reauthor_sources(
    root: str | Path,
    *,
    bead_ids: list[str],
    sweep: bool,
    limit: int,
    include_v1: bool,
    thin_only: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    index = _load_index(root)
    beads = {
        _clean(bead_id): dict(row)
        for bead_id, row in (index.get("beads") or {}).items()
        if _clean(bead_id) and isinstance(row, dict)
    }
    missing = [bead_id for bead_id in _dedupe_strings(bead_ids) if bead_id not in beads]
    if bead_ids:
        selected_ids = [bead_id for bead_id in _dedupe_strings(bead_ids) if bead_id in beads]
    elif sweep:
        v1_ids = _v1_bead_ids(root)
        selected_ids = []
        for bead_id, bead in beads.items():
            cohort = _cohort_for_bead(bead_id, bead, v1_bead_ids=v1_ids)
            if cohort == "backfilled" or (cohort == "v1_authored" and not include_v1):
                continue
            if thin_only and not _is_thin(bead):
                continue
            selected_ids.append(bead_id)
        selected_ids.sort(key=lambda bead_id: (_clean(beads[bead_id].get("created_at")), bead_id))
    else:
        selected_ids = []
    return [{"bead_id": bead_id, "bead": beads[bead_id]} for bead_id in selected_ids[: max(1, limit)]], missing


def _maintenance_attribution(
    *,
    action: str,
    actor: str,
    source_ref: str,
    task_authorship: dict[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "actor": actor,
        "contract_version": AGENT_AUTHORED_UPDATES_V1,
        "source_ref": source_ref,
        "authorship_source": _clean(task_authorship.get("source")) or "delegated_semantic_agent",
        "task_id": _clean(task_authorship.get("task_id")),
        "task_receipt_id": _clean(task_authorship.get("task_receipt_id")),
        "recorded_at": recorded_at,
    }


def _attach_maintenance_provenance(
    updates: dict[str, Any],
    *,
    action: str,
    actor: str,
    turn_id: str,
    task_authorship: dict[str, Any],
    source_bead_id: str = "",
    revision_type: str = "",
) -> dict[str, Any]:
    out = deepcopy(updates)
    recorded_at = _now()
    source_ref = f"bead:{source_bead_id}" if source_bead_id else f"turn:{turn_id}"
    attribution = _maintenance_attribution(
        action=action,
        actor=actor,
        source_ref=source_ref,
        task_authorship=task_authorship,
        recorded_at=recorded_at,
    )
    for row in [value for value in (out.get("beads_create") or []) if isinstance(value, dict)]:
        role = _clean(row.get("creation_role")).lower()
        if role == "current_turn" and action == "reauthor_memory":
            row["source_turn_ids"] = [turn_id]
            row["derived_from_bead_ids"] = _dedupe_strings([*(row.get("derived_from_bead_ids") or []), source_bead_id])
            row["derived_from"] = _dedupe_strings([*(row.get("derived_from") or []), source_ref])
            if revision_type:
                row["revises_bead_id"] = source_bead_id
                row["revision_type"] = revision_type
        row["source_refs"] = _dedupe_strings([*(row.get("source_refs") or []), source_ref])
        row["tags"] = _dedupe_strings(
            [
                *(row.get("tags") or []),
                BACKFILL_COHORT_TAG,
                BACKFILL_CONTRACT_TAG,
                action,
            ]
        )
        source_attribution = (
            dict(row.get("source_attribution")) if isinstance(row.get("source_attribution"), dict) else {}
        )
        source_attribution["core_memory_maintenance"] = dict(attribution)
        row["source_attribution"] = source_attribution
    if action == "reauthor_memory":
        # Reauthoring is append-only. The delegated author may recommend future
        # promotion in its semantic output, but this action cannot mutate the
        # source bead's governed promotion state.
        out["reviewed_beads"] = []
    return out


def _request_from_event(row: dict[str, Any]) -> dict[str, Any]:
    envelope = row.get("envelope") if isinstance(row.get("envelope"), dict) else {}
    return {
        "session_id": _clean(envelope.get("session_id")),
        "turn_id": _clean(envelope.get("turn_id")),
        "transaction_id": _clean(envelope.get("transaction_id")),
        "trace_id": _clean(envelope.get("trace_id")),
        "turns": list(envelope.get("turns") or []),
        "speakers": list(envelope.get("speakers") or []),
        "tools_trace": list(envelope.get("tools_trace") or []),
        "mesh_trace": list(envelope.get("mesh_trace") or []),
        "window_turn_ids": list(envelope.get("window_turn_ids") or []),
        "window_bead_ids": list(envelope.get("window_bead_ids") or []),
        "origin": _clean(envelope.get("origin")) or "USER_TURN",
        "metadata": dict(envelope.get("metadata") or {}),
    }


def _authoring_context(root: str, *, session_id: str, source_bead: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(source_bead, dict):
        bead_id = _clean(source_bead.get("id"))
        return {
            "session_id": session_id,
            "visible_bead_ids": [bead_id] if bead_id else [],
            "beads": [deepcopy(source_bead)],
        }
    from core_memory.association.crawler_contract import build_crawler_context

    return build_crawler_context(root=root, session_id=session_id, limit=200)


def _write_authored_turn(
    *,
    root: str,
    request: dict[str, Any],
    updates: dict[str, Any],
    task_authorship: dict[str, Any],
    maintenance_metadata: dict[str, Any],
) -> dict[str, Any]:
    from core_memory.runtime.engine import process_turn_finalized

    metadata = dict(request.get("metadata") or {})
    metadata.update(maintenance_metadata)
    return process_turn_finalized(
        root=root,
        session_id=_clean(request.get("session_id")),
        turn_id=_clean(request.get("turn_id")),
        transaction_id=_clean(request.get("transaction_id")) or None,
        trace_id=_clean(request.get("trace_id")) or None,
        turns=list(request.get("turns") or []),
        origin=_clean(request.get("origin")) or "USER_TURN",
        tools_trace=list(request.get("tools_trace") or []),
        mesh_trace=list(request.get("mesh_trace") or []),
        window_turn_ids=list(request.get("window_turn_ids") or []),
        window_bead_ids=list(request.get("window_bead_ids") or []),
        crawler_updates=updates,
        authoring_mode="inline",
        metadata=metadata,
        _authorship_provenance=task_authorship,
    )


def _association_after_commit(
    *,
    root: str,
    receipt: dict[str, Any],
    source_bead_ids: list[str],
    run_inline: bool,
) -> dict[str, Any]:
    if _clean(receipt.get("semantic_status")) != "committed":
        return {"ok": False, "status": "skipped", "reason": "semantic_write_not_committed"}
    bead_ids = _dedupe_strings([receipt.get("bead_id"), *((receipt.get("derived") or {}).get("bead_ids") or [])])
    if not bead_ids:
        return {"ok": False, "status": "skipped", "reason": "no_committed_beads"}
    from core_memory.runtime.associations.coverage import enqueue_association_coverage

    return enqueue_association_coverage(
        root=root,
        bead_ids=bead_ids,
        trigger="post_commit",
        candidate_bead_ids=_dedupe_strings(source_bead_ids),
        run_inline=bool(run_inline),
    )


def _association_counts(runs: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "runs": len(runs),
        "queued": 0,
        "pending_judge": 0,
        "judged": 0,
        "no_link": 0,
        "written": 0,
        "failed": 0,
    }
    for run in runs:
        status = _clean(run.get("status")).lower()
        counts = run.get("counts") if isinstance(run.get("counts"), dict) else {}
        out["queued"] += int(status == "queued")
        out["pending_judge"] += int(status == "pending_judge")
        out["judged"] += int(counts.get("accepted") or 0) + int(counts.get("rejected") or 0)
        out["no_link"] += int(counts.get("no_supported_links") or counts.get("no_link") or 0)
        out["written"] += int(counts.get("appended") or 0)
        out["failed"] += int(counts.get("failed") or 0) + int(status == "failed")
    return out


def _base_receipt(action: str, *, applied: bool, environment: str) -> dict[str, Any]:
    return {
        "ok": True,
        "contract": SEMANTIC_MAINTENANCE_RECEIPT_V1,
        "action": action,
        "applied": bool(applied),
        "environment": environment,
        "contract_version": AGENT_AUTHORED_UPDATES_V1,
        "source_mutation_policy": "immutable_append_only",
    }


def _copied_tenant_preflight_valid(
    value: Any,
    *,
    action: str,
    plan_fingerprint: str,
) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("ok")
        and value.get("applied")
        and _clean(value.get("environment")) == "copied_tenant"
        and _clean(value.get("action")) == action
        and _clean(value.get("operation_contract")) == SEMANTIC_MAINTENANCE_RECEIPT_V1
        and _clean(value.get("plan_fingerprint")) == plan_fingerprint
    )


def _copied_tenant_receipt_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    return {
        key: deepcopy(value.get(key))
        for key in (
            "ok",
            "applied",
            "action",
            "environment",
            "operation_contract",
            "plan_fingerprint",
            "idempotency_key",
        )
        if key in value
    } | {"receipt_fingerprint": _fingerprint(value)}


def reauthor_memory(
    *,
    root: str,
    bead_ids: list[str] | None = None,
    sweep: bool = False,
    limit: int = 50,
    include_v1: bool = False,
    thin_only: bool = True,
    revision_type: str = "",
    actor: str,
    environment: str = "local",
    apply: bool = False,
    idempotency_key: str = "",
    association_run_inline: bool = False,
    copied_tenant_validation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append richer agent-authored interpretations of existing beads."""

    action = "reauthor_memory"
    copied_receipt_summary = _copied_tenant_receipt_summary(copied_tenant_validation_receipt)
    source_rows, missing = _select_reauthor_sources(
        root,
        bead_ids=list(bead_ids or []),
        sweep=bool(sweep),
        limit=max(1, int(limit)),
        include_v1=bool(include_v1),
        thin_only=bool(thin_only),
    )
    request_payload = {
        "action": action,
        "bead_ids": [_clean(row.get("bead_id")) for row in source_rows],
        "missing": missing,
        "sweep": bool(sweep),
        "limit": max(1, int(limit)),
        "include_v1": bool(include_v1),
        "thin_only": bool(thin_only),
        "revision_type": revision_type,
        "environment": environment,
        "copied_tenant_validation_receipt": copied_receipt_summary,
    }
    plan_fingerprint = _fingerprint(
        {
            key: value
            for key, value in request_payload.items()
            if key not in {"environment", "copied_tenant_validation_receipt"}
        }
    )
    request_fingerprint = _fingerprint(request_payload)
    if apply:
        replay = _idempotent_replay(
            root,
            action=action,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay

    before = semantic_backfill_report(root)
    receipt = _base_receipt(action, applied=apply, environment=environment)
    receipt["plan_fingerprint"] = plan_fingerprint
    receipt["copied_tenant_validation_receipt"] = copied_receipt_summary
    receipt.update(
        {
            "idempotency_key": idempotency_key,
            "sources": {
                "examined": len(source_rows),
                "selected_bead_ids": [_clean(row.get("bead_id")) for row in source_rows],
                "missing_bead_ids": missing,
                "immutable": True,
            },
            "counts": {
                "authoring_attempted": 0,
                "committed": 0,
                "primary_writes": 0,
                "derived_writes": 0,
                "validation_failures": 0,
                "failed": 0,
            },
            "results": [],
            "associations": _association_counts([]),
            "cohorts": {"before": before, "after": before if not apply else None},
        }
    )
    if not apply:
        return receipt
    if environment == "live_tenant" and not _copied_tenant_preflight_valid(
        copied_tenant_validation_receipt,
        action=action,
        plan_fingerprint=plan_fingerprint,
    ):
        receipt.update(
            {
                "ok": False,
                "applied": False,
                "error": "copied_tenant_validation_required_before_live_apply",
            }
        )
        return receipt

    association_runs: list[dict[str, Any]] = []
    for source in source_rows:
        source_id = _clean(source.get("bead_id"))
        source_bead = deepcopy(source.get("bead")) if isinstance(source.get("bead"), dict) else {}
        source_bead["id"] = source_id
        turn_hash = hashlib.sha256(f"{idempotency_key}:{source_id}".encode("utf-8")).hexdigest()[:16]
        request = {
            "session_id": "semantic-backfill",
            "turn_id": f"reauthor-{turn_hash}",
            "transaction_id": f"tx-reauthor-{turn_hash}",
            "trace_id": f"tr-reauthor-{turn_hash}",
            "turns": [
                {
                    "speaker": "operator",
                    "role": "user",
                    "content": (
                        "Append an evidence-grounded richer interpretation of the visible source bead. "
                        "Do not rewrite or replace the source. Preserve uncertainty and return an honest thin "
                        "bead with retrieval_eligible=false when the source does not justify richer memory."
                    ),
                }
            ],
            "speakers": ["user"],
            "tools_trace": [],
            "mesh_trace": [],
            "window_turn_ids": [],
            "window_bead_ids": [source_id],
            "origin": "MEMORY_MAINTENANCE",
            "metadata": {},
        }
        try:
            updates, diag = author_turn_memory(
                root=root,
                req=request,
                crawler_context=_authoring_context(
                    root,
                    session_id=_clean(request.get("session_id")),
                    source_bead=source_bead,
                ),
                additional_instructions=(
                    f"This is governed reauthoring of source bead {source_id}. The current-turn bead must be an "
                    "append-only interpretation grounded in that source. Do not request mutation, promotion, "
                    "supersession, or deletion of the source bead."
                ),
                metadata={
                    "maintenance_action": action,
                    "source_bead_id": source_id,
                    "operator": actor,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report per-source failures and continue the bounded batch
            receipt["counts"]["authoring_attempted"] += 1
            receipt["counts"]["failed"] += 1
            receipt["results"].append(
                {
                    "source_bead_id": source_id,
                    "source_ref": f"bead:{source_id}",
                    "status": "failed",
                    "error": "semantic_author_exception",
                    "detail": str(exc),
                }
            )
            continue
        receipt["counts"]["authoring_attempted"] += 1
        task_authorship = dict(diag.get("authorship") or {})
        suppressed_reviews = (
            len([row for row in (updates.get("reviewed_beads") or []) if isinstance(row, dict)])
            if isinstance(updates, dict)
            else 0
        )
        result_row: dict[str, Any] = {
            "source_bead_id": source_id,
            "source_ref": f"bead:{source_id}",
            "authorship": task_authorship,
            "status": "failed",
        }
        if suppressed_reviews:
            result_row["suppressed_agent_outputs"] = {
                "reviewed_beads": suppressed_reviews,
                "reason": "reauthoring_is_append_only_and_cannot_mutate_source_promotion",
            }
        if not isinstance(updates, dict):
            receipt["counts"]["failed"] += 1
            result_row["error"] = _clean(diag.get("error_code")) or "semantic_author_unavailable"
            receipt["results"].append(result_row)
            continue
        prepared = _attach_maintenance_provenance(
            updates,
            action=action,
            actor=actor,
            turn_id=_clean(request.get("turn_id")),
            task_authorship=task_authorship,
            source_bead_id=source_id,
            revision_type=revision_type,
        )
        valid, error_code, validation = validate_agent_authored_updates(
            prepared,
            turn_id=_clean(request.get("turn_id")),
            require_v1=True,
        )
        result_row["validation"] = {"valid": valid, "error_code": error_code, "details": validation}
        if not valid:
            receipt["counts"]["validation_failures"] += 1
            receipt["counts"]["failed"] += 1
            result_row["error"] = error_code or "agent_updates_invalid"
            receipt["results"].append(result_row)
            continue
        try:
            write_result = _write_authored_turn(
                root=root,
                request=request,
                updates=prepared,
                task_authorship=task_authorship,
                maintenance_metadata={
                    "maintenance_action": action,
                    "maintenance_actor": actor,
                    "backfill_contract": SEMANTIC_MAINTENANCE_RECEIPT_V1,
                    "source_bead_id": source_id,
                    "source_immutable": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - source stays immutable; report this item and continue
            receipt["counts"]["failed"] += 1
            result_row.update(
                {
                    "status": "failed",
                    "error": "semantic_write_exception",
                    "detail": str(exc),
                }
            )
            receipt["results"].append(result_row)
            continue
        committed = _clean(write_result.get("semantic_status")) == "committed"
        result_row.update(
            {
                "status": "committed" if committed else _clean(write_result.get("semantic_status")) or "failed",
                "bead_id": _clean(write_result.get("bead_id")),
                "derived_bead_ids": list((write_result.get("derived") or {}).get("bead_ids") or []),
                "write_receipt": {
                    key: deepcopy(write_result.get(key))
                    for key in (
                        "contract",
                        "accepted",
                        "ok",
                        "retryable",
                        "semantic_status",
                        "error_code",
                        "bead_id",
                        "derived",
                    )
                    if key in write_result
                },
            }
        )
        if committed:
            receipt["counts"]["committed"] += 1
            receipt["counts"]["primary_writes"] += int(bool(write_result.get("bead_id")))
            receipt["counts"]["derived_writes"] += len(list((write_result.get("derived") or {}).get("bead_ids") or []))
            association_run = _association_after_commit(
                root=root,
                receipt=write_result,
                source_bead_ids=[source_id],
                run_inline=association_run_inline,
            )
            association_runs.append(association_run)
            result_row["association_run"] = association_run
        else:
            receipt["counts"]["failed"] += 1
        receipt["results"].append(result_row)

    receipt["associations"] = _association_counts(association_runs)
    receipt["cohorts"]["after"] = semantic_backfill_report(root)
    receipt["ok"] = receipt["counts"]["failed"] == 0
    _append_audit(
        root,
        action=action,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        actor=actor,
        source_refs=[f"bead:{row['bead_id']}" for row in source_rows],
        receipt=receipt,
    )
    return receipt


def _select_pending_turns(
    root: str | Path,
    *,
    session_id: str,
    turn_id: str,
    sweep: bool,
    limit: int,
) -> list[dict[str, Any]]:
    rows = list_semantic_write_states(root, statuses=_UNRESOLVED_STATUSES)
    if session_id:
        rows = [row for row in rows if _clean(row.get("session_id")) == session_id]
    if turn_id:
        rows = [row for row in rows if _clean(row.get("turn_id")) == turn_id]
    if not sweep and not (session_id and turn_id):
        return []
    return rows[: max(1, int(limit))]


def retry_pending_semantic(
    *,
    root: str,
    session_id: str = "",
    turn_id: str = "",
    sweep: bool = False,
    limit: int = 50,
    actor: str,
    environment: str = "local",
    apply: bool = False,
    idempotency_key: str = "",
    association_run_inline: bool = False,
    copied_tenant_validation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retry unresolved finalized turns through canonical semantic writing."""

    action = "retry_pending_semantic"
    copied_receipt_summary = _copied_tenant_receipt_summary(copied_tenant_validation_receipt)
    pending_rows = _select_pending_turns(
        root,
        session_id=_clean(session_id),
        turn_id=_clean(turn_id),
        sweep=bool(sweep),
        limit=max(1, int(limit)),
    )
    request_payload = {
        "action": action,
        "turns": [f"{_clean(row.get('session_id'))}:{_clean(row.get('turn_id'))}" for row in pending_rows],
        "sweep": bool(sweep),
        "limit": max(1, int(limit)),
        "environment": environment,
        "copied_tenant_validation_receipt": copied_receipt_summary,
    }
    plan_fingerprint = _fingerprint(
        {
            key: value
            for key, value in request_payload.items()
            if key not in {"environment", "copied_tenant_validation_receipt"}
        }
    )
    request_fingerprint = _fingerprint(request_payload)
    if apply:
        replay = _idempotent_replay(
            root,
            action=action,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay

    before = semantic_backfill_report(root)
    receipt = _base_receipt(action, applied=apply, environment=environment)
    receipt["plan_fingerprint"] = plan_fingerprint
    receipt["copied_tenant_validation_receipt"] = copied_receipt_summary
    receipt.update(
        {
            "idempotency_key": idempotency_key,
            "sources": {
                "examined": len(pending_rows),
                "turns": [
                    {
                        "session_id": _clean(row.get("session_id")),
                        "turn_id": _clean(row.get("turn_id")),
                        "pending_since": _clean(row.get("pending_since")),
                        "error_code": _clean(row.get("error_code")),
                    }
                    for row in pending_rows
                ],
            },
            "counts": {
                "authoring_attempted": 0,
                "committed": 0,
                "primary_writes": 0,
                "derived_writes": 0,
                "validation_failures": 0,
                "failed": 0,
            },
            "results": [],
            "associations": _association_counts([]),
            "cohorts": {"before": before, "after": before if not apply else None},
        }
    )
    if not apply:
        return receipt
    if environment == "live_tenant" and not _copied_tenant_preflight_valid(
        copied_tenant_validation_receipt,
        action=action,
        plan_fingerprint=plan_fingerprint,
    ):
        receipt.update(
            {
                "ok": False,
                "applied": False,
                "error": "copied_tenant_validation_required_before_live_apply",
            }
        )
        return receipt

    association_runs: list[dict[str, Any]] = []
    for pending in pending_rows:
        sid = _clean(pending.get("session_id"))
        tid = _clean(pending.get("turn_id"))
        event_row = event_for_turn(root, sid, tid)
        result_row: dict[str, Any] = {"session_id": sid, "turn_id": tid, "status": "failed"}
        if not isinstance(event_row, dict):
            receipt["counts"]["failed"] += 1
            result_row["error"] = "finalized_turn_event_not_found"
            receipt["results"].append(result_row)
            continue
        request = _request_from_event(event_row)
        try:
            updates, diag = author_turn_memory(
                root=root,
                req=request,
                crawler_context=_authoring_context(root, session_id=sid),
                additional_instructions=(
                    "This is an explicit pending-semantic retry. Author the canonical current-turn bead from the "
                    "preserved finalized turn. Do not fabricate missing evidence."
                ),
                metadata={
                    "maintenance_action": action,
                    "pending_session_id": sid,
                    "pending_turn_id": tid,
                    "operator": actor,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report per-turn failure and keep the turn retryable
            receipt["counts"]["authoring_attempted"] += 1
            receipt["counts"]["failed"] += 1
            result_row.update(
                {
                    "error": "semantic_author_exception",
                    "detail": str(exc),
                }
            )
            receipt["results"].append(result_row)
            continue
        receipt["counts"]["authoring_attempted"] += 1
        task_authorship = dict(diag.get("authorship") or {})
        result_row["authorship"] = task_authorship
        if not isinstance(updates, dict):
            receipt["counts"]["failed"] += 1
            result_row["error"] = _clean(diag.get("error_code")) or "semantic_author_unavailable"
            receipt["results"].append(result_row)
            continue
        prepared = _attach_maintenance_provenance(
            updates,
            action=action,
            actor=actor,
            turn_id=tid,
            task_authorship=task_authorship,
        )
        valid, error_code, validation = validate_agent_authored_updates(
            prepared,
            turn_id=tid,
            require_v1=True,
        )
        result_row["validation"] = {"valid": valid, "error_code": error_code, "details": validation}
        if not valid:
            receipt["counts"]["validation_failures"] += 1
            receipt["counts"]["failed"] += 1
            result_row["error"] = error_code or "agent_updates_invalid"
            receipt["results"].append(result_row)
            continue
        try:
            write_result = _write_authored_turn(
                root=root,
                request=request,
                updates=prepared,
                task_authorship=task_authorship,
                maintenance_metadata={
                    "maintenance_action": action,
                    "maintenance_actor": actor,
                    "backfill_contract": SEMANTIC_MAINTENANCE_RECEIPT_V1,
                    "retried_pending_semantic": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - pending state remains durable for another retry
            receipt["counts"]["failed"] += 1
            result_row.update(
                {
                    "status": "failed",
                    "error": "semantic_write_exception",
                    "detail": str(exc),
                }
            )
            receipt["results"].append(result_row)
            continue
        committed = _clean(write_result.get("semantic_status")) == "committed"
        result_row.update(
            {
                "status": "committed" if committed else _clean(write_result.get("semantic_status")) or "failed",
                "bead_id": _clean(write_result.get("bead_id")),
                "derived_bead_ids": list((write_result.get("derived") or {}).get("bead_ids") or []),
            }
        )
        if committed:
            receipt["counts"]["committed"] += 1
            receipt["counts"]["primary_writes"] += int(bool(write_result.get("bead_id")))
            receipt["counts"]["derived_writes"] += len(list((write_result.get("derived") or {}).get("bead_ids") or []))
            association_run = _association_after_commit(
                root=root,
                receipt=write_result,
                source_bead_ids=[],
                run_inline=association_run_inline,
            )
            association_runs.append(association_run)
            result_row["association_run"] = association_run
        else:
            receipt["counts"]["failed"] += 1
        receipt["results"].append(result_row)

    receipt["associations"] = _association_counts(association_runs)
    receipt["cohorts"]["after"] = semantic_backfill_report(root)
    receipt["ok"] = receipt["counts"]["failed"] == 0
    _append_audit(
        root,
        action=action,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        actor=actor,
        source_refs=[f"turn:{_clean(row.get('session_id'))}:{_clean(row.get('turn_id'))}" for row in pending_rows],
        receipt=receipt,
    )
    return receipt


__all__ = [
    "BACKFILL_COHORT_TAG",
    "SEMANTIC_BACKFILL_REPORT_V1",
    "SEMANTIC_MAINTENANCE_RECEIPT_V1",
    "reauthor_memory",
    "retry_pending_semantic",
    "semantic_backfill_report",
]
