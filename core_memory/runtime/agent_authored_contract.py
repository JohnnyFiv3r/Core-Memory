from __future__ import annotations

"""Agent-authored turn-memory contract.

Phase 3B makes the hot-path agent/adapter responsible for bead semantics.
Core Memory validates this shape at the write gate; it does not silently repair
or re-author invalid semantic fields unless an explicit legacy fallback is
selected elsewhere.
"""

from typing import Any

ERROR_AGENT_UPDATES_MISSING = "agent_updates_missing"
ERROR_AGENT_UPDATES_INVALID = "agent_updates_invalid"
ERROR_AGENT_ASSOCIATIONS_MISSING = "agent_associations_missing"
ERROR_AGENT_BEAD_FIELDS_MISSING = "agent_bead_fields_missing"
ERROR_AGENT_RETRIEVAL_FIELDS_MISSING = "agent_retrieval_fields_missing"
ERROR_AGENT_INVOCATION_EXHAUSTED = "agent_invocation_exhausted"
ERROR_AGENT_CALLABLE_MISSING = "agent_callable_missing"
ERROR_AGENT_SEMANTIC_COVERAGE_MISSING = "agent_semantic_coverage_missing"

SEMANTIC_BEAD_FIELDS = (
    "type",
    "title",
    "summary",
    "detail",
    "because",
    "retrieval_eligible",
    "retrieval_title",
    "retrieval_facts",
    "entities",
    "topics",
    "supporting_facts",
    "evidence_refs",
    "state_change",
    "validity",
    "effective_from",
    "effective_to",
    "observed_at",
)

AGENT_AUTHORED_REQUIRED_BEAD_FIELDS = (
    "type",
    "title",
    "summary",
    "retrieval_eligible",
    "retrieval_title",
    "retrieval_facts",
    "entities",
    "topics",
)

AGENT_AUTHORED_REQUIRED_ASSOC_FIELDS = (
    "source_bead_id",
    "target_bead_id",
    "relationship",
    "reason_text",
    "confidence",
)


def _text_present(value: Any) -> bool:
    return bool(str(value or "").strip())


def _list_text_present(value: Any) -> bool:
    if isinstance(value, list):
        return any(_text_present(v) for v in value)
    if isinstance(value, str):
        return _text_present(value)
    return False


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


def validate_agent_authored_updates(updates: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    """Strict shape gate for agent-authored crawler updates.

    Returns: (ok, error_code, details)
    """
    if not isinstance(updates, dict):
        return False, ERROR_AGENT_UPDATES_INVALID, {"reason": "updates_not_dict"}

    rows = updates.get("beads_create")
    if not isinstance(rows, list) or len(rows) != 1:
        return False, ERROR_AGENT_BEAD_FIELDS_MISSING, {
            "reason": "beads_create_must_have_exactly_one_row",
            "row_count": len(rows) if isinstance(rows, list) else None,
        }
    row = rows[0]
    if not isinstance(row, dict):
        return False, ERROR_AGENT_BEAD_FIELDS_MISSING, {"reason": "bead_row_not_object"}

    missing_bead = []
    for key in AGENT_AUTHORED_REQUIRED_BEAD_FIELDS:
        if key in {"retrieval_title", "retrieval_facts"}:
            continue
        if key == "summary":
            if not _list_text_present(row.get(key)):
                missing_bead.append(key)
        elif key in {"entities", "topics"}:
            if not _list_text_present(row.get(key)):
                missing_bead.append(key)
        else:
            if not _field_present(row.get(key)):
                missing_bead.append(key)
    if missing_bead:
        return False, ERROR_AGENT_BEAD_FIELDS_MISSING, {"missing_bead_fields": missing_bead}

    retrieval_eligible = _truthy_bool(row.get("retrieval_eligible"))
    missing_retrieval = []
    if retrieval_eligible:
        if not _text_present(row.get("retrieval_title")):
            missing_retrieval.append("retrieval_title")
        if not _list_text_present(row.get("retrieval_facts")):
            missing_retrieval.append("retrieval_facts")
    if missing_retrieval:
        return False, ERROR_AGENT_RETRIEVAL_FIELDS_MISSING, {"missing_retrieval_fields": missing_retrieval}

    assocs = updates.get("associations")
    if assocs is None:
        assocs = []
    if not isinstance(assocs, list):
        return False, ERROR_AGENT_ASSOCIATIONS_MISSING, {
            "reason": "associations_must_be_list_when_present",
            "assoc_count": None,
        }

    bad_rows = []
    for i, a in enumerate(assocs):
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

    return True, None, {
        "beads_create_count": 1,
        "associations_count": len(assocs),
        "retrieval_eligible": retrieval_eligible,
    }


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
        ],
        "semantic_bead_fields": list(SEMANTIC_BEAD_FIELDS),
        "required_bead_fields": list(AGENT_AUTHORED_REQUIRED_BEAD_FIELDS),
        "required_association_fields": list(AGENT_AUTHORED_REQUIRED_ASSOC_FIELDS),
        "retrieval_fields_required_when_retrieval_eligible": ["retrieval_title", "retrieval_facts"],
        "required_semantic_fields_slice_1": [
            "retrieval_eligible",
            "retrieval_title",
            "retrieval_facts",
            "entities",
            "topics",
        ],
        "beads_create_exactly_one": True,
        "associations_required": False,
    }
