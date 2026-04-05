from __future__ import annotations

"""Agent-authored turn-memory contract scaffolding.

Slice-0 defines stable error code constants and required semantic fields.
Runtime enforcement is introduced in follow-up slices.
"""

ERROR_AGENT_UPDATES_MISSING = "agent_updates_missing"
ERROR_AGENT_UPDATES_INVALID = "agent_updates_invalid"
ERROR_AGENT_ASSOCIATIONS_MISSING = "agent_associations_missing"
ERROR_AGENT_BEAD_FIELDS_MISSING = "agent_bead_fields_missing"

AGENT_AUTHORED_REQUIRED_BEAD_FIELDS = (
    "type",
    "title",
    "summary",
)

AGENT_AUTHORED_REQUIRED_ASSOC_FIELDS = (
    "source_bead_id",
    "target_bead_id",
    "relationship",
    "reason_text",
    "confidence",
)


def contract_snapshot() -> dict[str, object]:
    return {
        "error_codes": [
            ERROR_AGENT_UPDATES_MISSING,
            ERROR_AGENT_UPDATES_INVALID,
            ERROR_AGENT_ASSOCIATIONS_MISSING,
            ERROR_AGENT_BEAD_FIELDS_MISSING,
        ],
        "required_bead_fields": list(AGENT_AUTHORED_REQUIRED_BEAD_FIELDS),
        "required_association_fields": list(AGENT_AUTHORED_REQUIRED_ASSOC_FIELDS),
    }
