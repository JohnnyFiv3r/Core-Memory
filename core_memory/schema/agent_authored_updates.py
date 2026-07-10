"""Schema-derived field ownership for agent-authored bead creation rows.

The authored-update transport contract is expanded in the next delivery slice.
This module establishes its dependency direction now: schema owns the canonical
field inventory, while runtime and persistence import that inventory downward.
"""

from __future__ import annotations

from dataclasses import fields

from .models import Bead

BEAD_FIELD_NAMES = frozenset(field.name for field in fields(Bead))

# Values that Core Memory generates or advances as persistence mechanics.  A
# creation payload may carry a subset of the structural attachment fields as a
# compatibility input, but these values are overlaid by the runtime rather than
# treated as semantic authorship.
RUNTIME_OWNED_BEAD_FIELDS = frozenset(
    {
        "id",
        "created_at",
        "session_id",
        "source_turn_ids",
        "turn_index",
        "prev_bead_id",
        "next_bead_id",
        "status",
        "links",
        "recall_count",
        "last_recalled",
        "type_log",
        "type_coerced_from",
        "promoted_at",
        "promotion_locked",
        "promotion_score",
        "promotion_threshold",
        "confidence_class",
        "failure_signature",
        "validation_warnings",
        "decision_conflict_with",
        "unjustified_flip",
    }
)

# These remain readable for stored-data compatibility but are not new authored
# semantic fields. ``validity`` remains an accepted legacy field; the Ragie
# identifier is a sunset field and is never written by the authored path.
COMPATIBILITY_BEAD_FIELDS = frozenset({"validity", "ragie_document_id"})

AGENT_OWNED_BEAD_FIELDS = frozenset(
    BEAD_FIELD_NAMES - RUNTIME_OWNED_BEAD_FIELDS - COMPATIBILITY_BEAD_FIELDS
)

# Control fields exist only on the creation envelope and are never bead state.
# Only ``creation_role`` is agent-authored; identifiers and source references
# are populated after strict authored-payload validation.
AUTHORED_CREATION_CONTROL_FIELDS = frozenset({"creation_role"})
RUNTIME_CREATION_CONTROL_FIELDS = frozenset(
    {"bead_id", "turn_id", "source_turn_ref"}
)
CREATION_CONTROL_FIELDS = frozenset(
    AUTHORED_CREATION_CONTROL_FIELDS | RUNTIME_CREATION_CONTROL_FIELDS
)

# Runtime-populated structural values accepted on the compatibility ingress.
# They are deliberately separate from agent-owned semantic fields.
CREATION_STRUCTURAL_INPUT_FIELDS = frozenset(
    {
        "source_turn_ids",
        "turn_index",
        "prev_bead_id",
    }
)

AUTHORED_CREATION_ROW_FIELDS = frozenset(
    AGENT_OWNED_BEAD_FIELDS
    | AUTHORED_CREATION_CONTROL_FIELDS
    | CREATION_STRUCTURAL_INPUT_FIELDS
    | {"validity"}
)

# Runtime invariants add these before the persistence normalizer runs. They are
# accepted at that internal boundary, then replaced/ignored by the overlay.
CREATION_RUNTIME_OVERLAY_INPUT_FIELDS = frozenset({"created_at", "session_id"})
NORMALIZABLE_CREATION_ROW_FIELDS = frozenset(
    AUTHORED_CREATION_ROW_FIELDS
    | CREATION_CONTROL_FIELDS
    | CREATION_RUNTIME_OVERLAY_INPUT_FIELDS
)


def bead_field_ownership_snapshot() -> dict[str, list[str]]:
    """Return the complete, testable Bead-field ownership inventory."""

    return {
        "agent_owned": sorted(AGENT_OWNED_BEAD_FIELDS),
        "runtime_owned": sorted(RUNTIME_OWNED_BEAD_FIELDS),
        "compatibility": sorted(COMPATIBILITY_BEAD_FIELDS),
    }


__all__ = [
    "AGENT_OWNED_BEAD_FIELDS",
    "AUTHORED_CREATION_CONTROL_FIELDS",
    "AUTHORED_CREATION_ROW_FIELDS",
    "BEAD_FIELD_NAMES",
    "COMPATIBILITY_BEAD_FIELDS",
    "CREATION_CONTROL_FIELDS",
    "CREATION_RUNTIME_OVERLAY_INPUT_FIELDS",
    "CREATION_STRUCTURAL_INPUT_FIELDS",
    "NORMALIZABLE_CREATION_ROW_FIELDS",
    "RUNTIME_OWNED_BEAD_FIELDS",
    "RUNTIME_CREATION_CONTROL_FIELDS",
    "bead_field_ownership_snapshot",
]
