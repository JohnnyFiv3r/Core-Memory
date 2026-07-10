"""Build the canonical processed turn-write receipt from durable state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core_memory.persistence.store_claim_ops import find_canonical_turn_bead_id
from core_memory.runtime.associations.coverage import latest_association_coverage
from core_memory.runtime.turn.semantic_state import (
    event_for_turn,
    get_semantic_flush_waiver,
    get_semantic_write_state,
    mark_semantic_write_state,
)
from core_memory.schema.turn_receipt import (
    TURN_FINALIZED_RECEIPT_V2,
    AssociationStatus,
    SemanticStatus,
    TurnFinalizedReceiptV2,
)


def public_association_status(internal_state: str, *, bead_exists: bool = True) -> AssociationStatus:
    """Map internal association lifecycle values into the five public states."""

    state = str(internal_state or "").strip().lower()
    if not bead_exists:
        return "skipped"
    if state in {"linked", "no_supported_links", "no_link", "completed", "complete"}:
        return "complete"
    if state in {"pending_judge"}:
        return "pending_judge"
    if state in {"judge_failed", "quarantined", "failed", "coverage_failed"}:
        return "failed"
    if state in {"skipped", "skipped_ineligible", "ineligible"}:
        return "skipped"
    return "pending"


def _event_id(root: str, session_id: str, turn_id: str, result: dict[str, Any]) -> str:
    emitted = dict(result.get("emitted") or {})
    event_id = str(emitted.get("event_id") or ((emitted.get("payload") or {}).get("event") or {}).get("event_id") or "")
    if event_id:
        return event_id
    prior = get_semantic_write_state(root, session_id, turn_id) or {}
    event_id = str(prior.get("event_id") or "")
    if event_id:
        return event_id
    row = event_for_turn(root, session_id, turn_id) or {}
    return str((row.get("event") or {}).get("event_id") or "")


def _association_receipt(root: str, bead_id: str) -> dict[str, Any]:
    if not bead_id:
        return {"status": "skipped", "candidates": 0, "judged": 0, "written": 0, "pending": 0}
    coverage = latest_association_coverage(root, bead_id)
    internal = str(coverage.get("state") or coverage.get("status") or "unknown")
    status = public_association_status(internal, bead_exists=True)
    counts = dict(coverage.get("counts") or {})
    candidates = int(coverage.get("candidate_count") or counts.get("candidates") or 0)
    judged = (
        int(counts.get("accepted") or 0) + int(counts.get("rejected") or 0) + int(counts.get("no_supported_links") or 0)
    )
    written = int(counts.get("appended") or coverage.get("appended") or 0)
    pending = 1 if status in {"pending", "pending_judge"} else 0
    return {
        "status": status,
        "candidates": candidates,
        "judged": judged,
        "written": written,
        "pending": pending,
    }


def _queue_receipt(result: dict[str, Any]) -> dict[str, Any]:
    if bool(result.get("gate_blocked")):
        return {"status": "skipped", "item_id": ""}
    queue_item = dict(result.get("enrichment_queue") or {})
    item_id = str(queue_item.get("id") or "")
    if not bool(result.get("enrichment_queued")):
        return {"status": "drained", "item_id": item_id}
    drain = dict(result.get("enrichment_drain") or {})
    for item in list(drain.get("item_results") or drain.get("results") or []):
        if not isinstance(item, dict) or str(item.get("kind") or "") != "turn-enrichment":
            continue
        item_id = str(item.get("id") or item_id)
        nested = dict(item.get("result") or {})
        if bool(nested.get("ok", True)):
            return {"status": "drained", "item_id": item_id}
    if int(drain.get("failed") or 0) > 0:
        return {"status": "failed", "item_id": item_id}
    return {"status": "pending", "item_id": item_id}


def _creation_result(result: dict[str, Any]) -> dict[str, Any]:
    return dict(((result.get("crawler_handoff") or {}).get("association_pass") or {}))


def _validation_receipt(result: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    gate = dict(((result.get("crawler_handoff") or {}).get("agent_authored_gate") or {}))
    creation = _creation_result(result)
    if not gate and not creation and isinstance(prior.get("validation"), dict):
        return deepcopy(dict(prior["validation"]))
    diagnostics = [dict(row) for row in (creation.get("creation_diagnostics") or []) if isinstance(row, dict)]
    warnings = [dict(row) for row in (gate.get("warnings") or []) if isinstance(row, dict)]
    warnings.extend(row for row in diagnostics if str(row.get("code") or "") != "retrieval_eligibility_downgraded")
    downgrades = [row for row in diagnostics if str(row.get("code") or "") == "retrieval_eligibility_downgraded"]
    return {
        "valid": not bool(gate.get("blocked")),
        "warnings": warnings,
        "downgrades": downgrades,
    }


def _authorship_receipt(result: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    gate = dict(((result.get("crawler_handoff") or {}).get("agent_authored_gate") or {}))
    if not gate and isinstance(prior.get("authorship"), dict):
        return deepcopy(dict(prior["authorship"]))
    authorship = deepcopy(dict(gate.get("authorship") or {}))
    authorship.setdefault("source", str(gate.get("source") or "unknown"))
    authorship.setdefault("schema_version", str(authorship.get("schema_version") or ""))
    authorship["used_fallback"] = bool(authorship.get("used_fallback", gate.get("used_fallback", False)))
    authorship["repair_used"] = bool(authorship.get("repair_used", False))
    authorship["repaired_fields"] = [str(x) for x in (authorship.get("repaired_fields") or []) if str(x)]
    return authorship


def build_turn_finalized_receipt(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    result: dict[str, Any],
) -> TurnFinalizedReceiptV2:
    """Resolve canonical semantic truth, persist it, and return the v2 receipt."""

    creation = _creation_result(result)
    preferred = [
        str(creation.get("current_turn_bead_id") or result.get("bead_id") or ""),
        *[str(x) for x in (creation.get("created_bead_ids") or []) if str(x)],
    ]
    bead_id = str(
        find_canonical_turn_bead_id(
            root,
            session_id=session_id,
            turn_id=turn_id,
            preferred_bead_ids=[x for x in preferred if x],
        )
        or ""
    )
    prior = get_semantic_write_state(root, session_id, turn_id) or {}
    event_id = _event_id(root, session_id, turn_id, result)
    validation = _validation_receipt(result, prior)
    gate_blocked = bool(result.get("gate_blocked"))
    accepted = bool(event_id or str((result.get("emitted") or {}).get("reason") or "") == "idempotent_done")

    gate_error_code = str(result.get("error_code") or "")
    unavailable_codes = {
        "agent_updates_missing",
        "agent_callable_missing",
        "agent_invocation_exhausted",
    }
    if gate_blocked and accepted:
        semantic_status: SemanticStatus = "pending" if gate_error_code in unavailable_codes else "repair_required"
    elif bead_id:
        semantic_status = "committed"
    elif str(prior.get("status") or "") == "waived" and get_semantic_flush_waiver(root, session_id, turn_id):
        semantic_status = "waived"
    elif accepted:
        prior_unresolved = str(prior.get("status") or "")
        semantic_status = prior_unresolved if prior_unresolved in {"pending", "repair_required"} else "pending"
    else:
        semantic_status = "failed"

    associations = _association_receipt(root, bead_id)
    queue = _queue_receipt(result)
    derived_failures = [dict(row) for row in (creation.get("derived_failures") or []) if isinstance(row, dict)]
    derived_ids = [str(x) for x in (creation.get("derived_bead_ids") or []) if str(x)]
    retryable = semantic_status in {"pending", "repair_required"} or queue["status"] in {"pending", "failed"}
    error_code = str(result.get("error_code") or "")
    if semantic_status == "pending" and not error_code:
        error_code = "semantic_write_pending"
    elif semantic_status == "failed" and not error_code:
        error_code = str(result.get("error") or "semantic_write_failed")

    receipt: TurnFinalizedReceiptV2 = {
        "contract": TURN_FINALIZED_RECEIPT_V2,
        "accepted": accepted,
        "ok": semantic_status in {"committed", "waived"},
        "retryable": bool(retryable),
        "session_id": str(session_id),
        "turn_id": str(turn_id),
        "event_id": event_id,
        "bead_id": bead_id,
        "semantic_status": semantic_status,
        "authorship": _authorship_receipt(result, prior),
        "validation": validation,
        "associations": associations,
        "queue": queue,
        "derived": {
            "written": len(derived_ids),
            "bead_ids": derived_ids,
            "failures": derived_failures,
        },
    }
    if error_code:
        receipt["error_code"] = error_code

    mark_semantic_write_state(
        root,
        session_id=session_id,
        turn_id=turn_id,
        status=semantic_status,
        event_id=event_id,
        bead_id=bead_id,
        retryable=bool(retryable),
        error_code=error_code,
        derived_failures=derived_failures,
        association_status=str(associations.get("status") or ""),
        queue_status=str(queue.get("status") or ""),
        authorship=dict(receipt.get("authorship") or {}),
        validation=dict(receipt.get("validation") or {}),
        association_receipt=dict(receipt.get("associations") or {}),
        queue_receipt=dict(receipt.get("queue") or {}),
        reason="turn_finalized_receipt",
    )
    return receipt


def receipt_view(result: dict[str, Any]) -> TurnFinalizedReceiptV2:
    """Return only the stable v2 receipt fields from a processed engine result."""

    keys = {
        "contract",
        "accepted",
        "ok",
        "retryable",
        "session_id",
        "turn_id",
        "event_id",
        "bead_id",
        "semantic_status",
        "error_code",
        "error",
        "authorship",
        "validation",
        "associations",
        "queue",
        "derived",
    }
    return {key: deepcopy(value) for key, value in result.items() if key in keys}  # type: ignore[return-value]


def rejected_turn_receipt(*, session_id: str, turn_id: str, error_code: str) -> TurnFinalizedReceiptV2:
    """Return the stable v2 shape for input rejected before event acceptance."""

    return {
        "contract": TURN_FINALIZED_RECEIPT_V2,
        "accepted": False,
        "ok": False,
        "retryable": False,
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
        "event_id": "",
        "bead_id": "",
        "semantic_status": "failed",
        "error_code": str(error_code or "invalid_turn_write"),
        "error": str(error_code or "invalid_turn_write"),
        "authorship": {
            "source": "unknown",
            "schema_version": "",
            "used_fallback": False,
            "repair_used": False,
            "repaired_fields": [],
        },
        "validation": {"valid": False, "warnings": [], "downgrades": []},
        "associations": {"status": "skipped", "candidates": 0, "judged": 0, "written": 0, "pending": 0},
        "queue": {"status": "skipped", "item_id": ""},
        "derived": {"written": 0, "bead_ids": [], "failures": []},
    }


__all__ = [
    "build_turn_finalized_receipt",
    "public_association_status",
    "rejected_turn_receipt",
    "receipt_view",
]
