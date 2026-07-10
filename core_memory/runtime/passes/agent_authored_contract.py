"""Agent-authored turn-memory contract.

Phase 3B makes the hot-path agent/adapter responsible for bead semantics.
Core Memory validates this shape at the write gate; it does not silently repair
or re-author invalid semantic fields unless an explicit legacy fallback is
selected elsewhere.
"""

from __future__ import annotations

from typing import Any

from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS,
    AGENT_AUTHORED_UPDATES_V1,
    AUTHORED_CREATION_ROW_FIELDS,
    validate_agent_authored_updates_v1_transport,
)
from core_memory.schema.agent_authored_updates import (
    AGENT_AUTHORED_REQUIRED_BEAD_FIELDS as V1_REQUIRED_BEAD_FIELDS,
)

ERROR_AGENT_UPDATES_MISSING = "agent_updates_missing"
ERROR_AGENT_UPDATES_INVALID = "agent_updates_invalid"
ERROR_AGENT_ASSOCIATIONS_MISSING = "agent_associations_missing"
ERROR_AGENT_BEAD_FIELDS_MISSING = "agent_bead_fields_missing"
ERROR_AGENT_RETRIEVAL_FIELDS_MISSING = "agent_retrieval_fields_missing"
ERROR_AGENT_INVOCATION_EXHAUSTED = "agent_invocation_exhausted"
ERROR_AGENT_CALLABLE_MISSING = "agent_callable_missing"
ERROR_AGENT_SEMANTIC_COVERAGE_MISSING = "agent_semantic_coverage_missing"
ERROR_AGENT_CAUSAL_RATIONALE_MISSING = "agent_causal_rationale_missing"

CAUSAL_BEAD_TYPES = {"decision", "outcome", "precedent", "design_principle", "lesson"}

SEMANTIC_BEAD_FIELDS = (
    "type",
    "title",
    "summary",
    "detail",
    "because",
    "entities",
    "supporting_facts",
    "evidence_refs",
    "state_change",
    "effective_from",
    "effective_to",
    "observed_at",
)

LEGACY_REQUIRED_BEAD_FIELDS = (
    "type",
    "title",
    "summary",
    "entities",
    "retrieval_eligible",
)


def _text_present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _list_text_present(value: Any) -> bool:
    return isinstance(value, list) and any(_text_present(v) for v in value)


def _field_present(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, list):
        return True
    if value is None:
        return False
    return _text_present(value)


def _truthy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _confidence_valid(value: Any) -> bool:
    try:
        c = float(value)
    except Exception:
        return False
    return 0.0 <= c <= 1.0


def validate_agent_authored_updates(
    updates: dict[str, Any],
    *,
    max_create_per_turn: int | None = None,
    turn_id: str = "",
    require_v1: bool = False,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Strict shape gate for agent-authored crawler updates.

    Returns: (ok, error_code, details)
    """
    if not isinstance(updates, dict):
        return False, ERROR_AGENT_UPDATES_INVALID, {"reason": "updates_not_dict"}

    is_v1 = str(updates.get("schema_version") or "") == AGENT_AUTHORED_UPDATES_V1
    if require_v1 and not is_v1:
        return (
            False,
            ERROR_AGENT_UPDATES_INVALID,
            {
                "reason": "agent_authored_updates_v1_required",
                "expected_schema_version": AGENT_AUTHORED_UPDATES_V1,
            },
        )
    if is_v1:
        transport_ok, transport_errors = validate_agent_authored_updates_v1_transport(updates)
        if not transport_ok:
            return (
                False,
                ERROR_AGENT_UPDATES_INVALID,
                {
                    "reason": "agent_authored_updates_v1_transport_invalid",
                    "transport_errors": transport_errors,
                },
            )

    rows = updates.get("beads_create")
    if not isinstance(rows, list) or len(rows) < 1:
        return (
            False,
            ERROR_AGENT_BEAD_FIELDS_MISSING,
            {
                "reason": "beads_create_must_have_at_least_one_row",
                "row_count": len(rows) if isinstance(rows, list) else None,
            },
        )
    # ``max_create_per_turn`` remains accepted for source compatibility, but
    # semantic cardinality is a property of agent_authored_updates.v1 rather
    # than an optional SidecarPolicy instance.
    _ = max_create_per_turn

    if is_v1:
        current_rows = [
            index
            for index, row in enumerate(rows)
            if isinstance(row, dict) and str(row.get("creation_role") or "").strip().lower() == "current_turn"
        ]
        derived_rows = [
            index
            for index, row in enumerate(rows)
            if isinstance(row, dict) and str(row.get("creation_role") or "").strip().lower() == "derived"
        ]
        if len(current_rows) != 1:
            return (
                False,
                ERROR_AGENT_BEAD_FIELDS_MISSING,
                {
                    "reason": "current_turn_cardinality_invalid",
                    "current_turn_count": len(current_rows),
                    "required": 1,
                },
            )
        if len(derived_rows) > 2 or len(rows) != len(current_rows) + len(derived_rows):
            return (
                False,
                ERROR_AGENT_BEAD_FIELDS_MISSING,
                {
                    "reason": "derived_cardinality_invalid",
                    "derived_count": len(derived_rows),
                    "max_derived_rows": 2,
                },
            )

    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            return False, ERROR_AGENT_BEAD_FIELDS_MISSING, {"reason": "bead_row_not_object", "row_index": row_index}

        unknown_fields = sorted(str(key) for key in row if key not in AUTHORED_CREATION_ROW_FIELDS)
        if unknown_fields:
            return (
                False,
                ERROR_AGENT_UPDATES_INVALID,
                {
                    "reason": "unknown_authored_bead_fields",
                    "row_index": row_index,
                    "unknown_fields": unknown_fields,
                },
            )

        missing_bead = []
        required_bead_fields = V1_REQUIRED_BEAD_FIELDS if is_v1 else LEGACY_REQUIRED_BEAD_FIELDS
        for key in required_bead_fields:
            if key == "summary":
                if not _list_text_present(row.get(key)):
                    missing_bead.append(key)
            elif key == "entities":
                if (not isinstance(row.get(key), list)) if is_v1 else (not _list_text_present(row.get(key))):
                    missing_bead.append(key)
            elif key == "source_turn_ids":
                if (not isinstance(row.get(key), list)) if is_v1 else (not _list_text_present(row.get(key))):
                    missing_bead.append(key)
            else:
                if not _field_present(row.get(key)):
                    missing_bead.append(key)
        if missing_bead:
            return False, ERROR_AGENT_BEAD_FIELDS_MISSING, {"row_index": row_index, "missing_bead_fields": missing_bead}

        if is_v1:
            role = str(row.get("creation_role") or "").strip().lower()
            source_turn_ids = [str(item) for item in (row.get("source_turn_ids") or []) if str(item).strip()]
            expected_turn_id = str(turn_id or "").strip()
            if role == "current_turn" and expected_turn_id and expected_turn_id not in source_turn_ids:
                return (
                    False,
                    ERROR_AGENT_BEAD_FIELDS_MISSING,
                    {
                        "reason": "current_turn_source_turn_missing",
                        "row_index": row_index,
                        "turn_id": expected_turn_id,
                    },
                )
            if role == "derived":
                derived_from = [str(item) for item in (row.get("derived_from_bead_ids") or []) if str(item).strip()]
                if derived_from != ["$current_turn"]:
                    return (
                        False,
                        ERROR_AGENT_BEAD_FIELDS_MISSING,
                        {
                            "reason": "derived_current_turn_link_invalid",
                            "row_index": row_index,
                            "expected": ["$current_turn"],
                            "actual": derived_from,
                        },
                    )
                if expected_turn_id and expected_turn_id in source_turn_ids:
                    return (
                        False,
                        ERROR_AGENT_BEAD_FIELDS_MISSING,
                        {
                            "reason": "derived_row_claims_current_turn_source",
                            "row_index": row_index,
                            "turn_id": expected_turn_id,
                        },
                    )

        bead_type = str(row.get("type") or "").strip().lower()
        if bead_type in CAUSAL_BEAD_TYPES and not _list_text_present(row.get("because")):
            return (
                False,
                ERROR_AGENT_CAUSAL_RATIONALE_MISSING,
                {
                    "row_index": row_index,
                    "type": bead_type,
                    "missing_bead_fields": ["because"],
                    "causal_types_require_because": sorted(CAUSAL_BEAD_TYPES),
                },
            )

    assocs = updates.get("associations")
    if assocs is None:
        assocs = []
    if not isinstance(assocs, list):
        return (
            False,
            ERROR_AGENT_ASSOCIATIONS_MISSING,
            {
                "reason": "associations_must_be_list_when_present",
                "assoc_count": None,
            },
        )

    bad_rows = []
    for i, a in enumerate(assocs or []):
        if not isinstance(a, dict):
            bad_rows.append({"index": i, "reason": "association_not_object"})
            continue
        src = a.get("source_bead_id") if a.get("source_bead_id") is not None else a.get("source_bead")
        tgt = a.get("target_bead_id") if a.get("target_bead_id") is not None else a.get("target_bead")
        if not _text_present(src):
            bad_rows.append({"index": i, "reason": "missing_source_bead_id"})
            continue
        if not _text_present(tgt):
            bad_rows.append({"index": i, "reason": "missing_target_bead_id"})
            continue
        if not _text_present(a.get("relationship")):
            bad_rows.append({"index": i, "reason": "missing_relationship"})
            continue
        if not _text_present(a.get("reason_text")):
            bad_rows.append({"index": i, "reason": "missing_reason_text"})
            continue
        if not _confidence_valid(a.get("confidence")):
            bad_rows.append({"index": i, "reason": "invalid_confidence"})
            continue

    if bad_rows:
        return False, ERROR_AGENT_UPDATES_INVALID, {"bad_association_rows": bad_rows[:10]}

    return (
        True,
        None,
        {
            "beads_create_count": len(rows),
            "associations_count": len(assocs),
        },
    )


def contract_snapshot() -> dict[str, object]:
    return {
        "error_codes": [
            ERROR_AGENT_UPDATES_MISSING,
            ERROR_AGENT_UPDATES_INVALID,
            ERROR_AGENT_ASSOCIATIONS_MISSING,
            ERROR_AGENT_BEAD_FIELDS_MISSING,
            ERROR_AGENT_RETRIEVAL_FIELDS_MISSING,
            ERROR_AGENT_INVOCATION_EXHAUSTED,
            ERROR_AGENT_CALLABLE_MISSING,
            ERROR_AGENT_SEMANTIC_COVERAGE_MISSING,
            ERROR_AGENT_CAUSAL_RATIONALE_MISSING,
        ],
        "semantic_bead_fields": list(SEMANTIC_BEAD_FIELDS),
        "required_bead_fields": list(LEGACY_REQUIRED_BEAD_FIELDS),
        "v1_required_bead_fields": list(V1_REQUIRED_BEAD_FIELDS),
        "required_association_fields": list(AGENT_AUTHORED_REQUIRED_ASSOCIATION_FIELDS),
        "causal_types_require_because": sorted(CAUSAL_BEAD_TYPES),
        "summary_shape": "list[str]",
        "beads_create_exactly_one": False,
        "beads_create_min": 1,
        "beads_create_max": 3,
        "current_turn_exactly_one": True,
        "current_turn_rows": 1,
        "max_derived_rows": 2,
        "beads_create_max_policy": "contract_owned_not_sidecar_policy",
        "unknown_bead_fields": "reject",
        "associations_required": False,
    }
