"""
Single-source definition registry for all Claim Layer canonical labels.
Runtime and prompt surfaces should consume from here.
"""
from __future__ import annotations

# --- Bead Types ---
from core_memory.schema.normalization import PUBLIC_CATALOG_BEAD_TYPES

BEAD_TYPE_DEFINITIONS: dict[str, str] = {
    bead_type: f"Semantic bead type: {bead_type}"
    for bead_type in PUBLIC_CATALOG_BEAD_TYPES
}

# --- Claim Kinds ---
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS
from core_memory.claim.prompt_definitions import CLAIM_KIND_DEFINITIONS

# --- Claim Update Decisions ---
from core_memory.schema.normalization import CLAIM_UPDATE_DECISIONS
from core_memory.claim.prompt_definitions import CLAIM_UPDATE_DECISION_DEFINITIONS

# --- Memory Interaction Roles ---
from core_memory.claim.outcomes import INTERACTION_ROLES

MEMORY_INTERACTION_ROLE_DEFINITIONS: dict[str, str] = INTERACTION_ROLES

# --- Retrieval Modes ---
from core_memory.claim.retrieval_planner import RETRIEVAL_MODES

RETRIEVAL_MODE_DEFINITIONS: dict[str, str] = RETRIEVAL_MODES

# --- Answer Outcomes ---
from core_memory.claim.answer_policy import ANSWER_OUTCOMES

ANSWER_OUTCOME_DEFINITIONS: dict[str, str] = ANSWER_OUTCOMES

# --- Lifecycle Status ---
LIFECYCLE_STATUS_DEFINITIONS: dict[str, str] = {
    "default": "Normal active state — bead is visible and current.",
    "archived": "Bead has been archived — excluded from default retrieval.",
    "superseded": "Bead has been superseded by a newer version.",
}

# --- Promotion States ---
PROMOTION_STATE_DEFINITIONS: dict[str, str] = {
    "null": "No promotion state — bead has not been evaluated for promotion.",
    "candidate": "Bead is a candidate for promotion to long-term memory.",
    "promoted": "Bead has been promoted to long-term memory.",
}

# --- Relation Labels ---
from core_memory.schema.normalization import AGENT_FACING_RELATION_TYPES

RELATION_LABEL_DEFINITIONS: dict[str, str] = {
    label: f"Relation type: {label}"
    for label in AGENT_FACING_RELATION_TYPES
}


def get_all_definitions() -> dict[str, dict[str, str]]:
    """Return all definitions grouped by category."""
    return {
        "bead_types": BEAD_TYPE_DEFINITIONS,
        "claim_kinds": CLAIM_KIND_DEFINITIONS,
        "claim_update_decisions": CLAIM_UPDATE_DECISION_DEFINITIONS,
        "memory_interaction_roles": MEMORY_INTERACTION_ROLE_DEFINITIONS,
        "retrieval_modes": RETRIEVAL_MODE_DEFINITIONS,
        "answer_outcomes": ANSWER_OUTCOME_DEFINITIONS,
        "lifecycle_statuses": LIFECYCLE_STATUS_DEFINITIONS,
        "promotion_states": PROMOTION_STATE_DEFINITIONS,
        "relation_labels": RELATION_LABEL_DEFINITIONS,
    }
