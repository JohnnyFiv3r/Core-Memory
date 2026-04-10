"""
Single-source definition registry for claim-layer canonical labels.
Runtime and prompt surfaces should consume from here.
"""
from __future__ import annotations

from core_memory.claim.answer_policy import ANSWER_OUTCOMES
from core_memory.claim.outcomes import INTERACTION_ROLES
from core_memory.claim.prompt_definitions import CLAIM_KIND_DEFINITIONS, CLAIM_UPDATE_DECISION_DEFINITIONS
from core_memory.claim.retrieval_planner import RETRIEVAL_MODES
from core_memory.schema.normalization import (
    AGENT_FACING_RELATION_TYPES,
    CANONICAL_CLAIM_KINDS,
    CLAIM_UPDATE_DECISIONS,
    PUBLIC_CATALOG_BEAD_TYPES,
)


_BEAD_OVERRIDES: dict[str, str] = {
    "goal": "Desired end-state; captures what should be achieved.",
    "decision": "Chosen action or policy; records what was selected and why.",
    "tool_call": "Tool invocation intent and result context from a turn.",
    "evidence": "Observations or facts that support/refute a decision or claim.",
    "outcome": "Observed result after an action or decision.",
    "lesson": "Generalized takeaway that should transfer to future work.",
    "checkpoint": "State snapshot marker for continuity and progress framing.",
    "precedent": "Past case used as a reusable analog for current reasoning.",
    "failed_hypothesis": "A tested idea that did not hold.",
    "reversal": "A deliberate change from a previously held direction.",
    "misjudgment": "An identified error in judgment or estimation.",
    "overfitted_pattern": "A pattern that looked right locally but failed to generalize.",
    "abandoned_path": "A route explored then intentionally dropped.",
    "reflection": "Meta-reasoning about process quality and future adjustment.",
    "design_principle": "Stable implementation principle that should guide future decisions.",
    "context": "Situational framing/background. Not a fallback bucket for missing structure.",
    "correction": "A direct correction to previously asserted understanding.",
}

BEAD_TYPE_DEFINITIONS: dict[str, str] = {
    bead_type: _BEAD_OVERRIDES.get(bead_type, f"Semantic bead type `{bead_type}` used for structured turn memory.")
    for bead_type in sorted(PUBLIC_CATALOG_BEAD_TYPES)
}


# Claim kinds / update decisions come from dedicated prompt-definition source.
# Keep explicit linkage to schema constants for drift visibility.
_UNUSED_CLAIM_KINDS = CANONICAL_CLAIM_KINDS
_UNUSED_CLAIM_UPDATE_DECISIONS = CLAIM_UPDATE_DECISIONS


MEMORY_INTERACTION_ROLE_DEFINITIONS: dict[str, str] = dict(INTERACTION_ROLES)
RETRIEVAL_MODE_DEFINITIONS: dict[str, str] = dict(RETRIEVAL_MODES)
ANSWER_OUTCOME_DEFINITIONS: dict[str, str] = dict(ANSWER_OUTCOMES)


LIFECYCLE_STATUS_DEFINITIONS: dict[str, str] = {
    "default": "Active storage state for normal retrieval and continuity operations.",
    "archived": "Archived from active surface but still authoritative and retrievable by bead_id.",
    "superseded": "Replaced by newer canonical memory; kept for historical traceability.",
}

PROMOTION_STATE_DEFINITIONS: dict[str, str] = {
    "null": "No promotion judgment applied yet.",
    "candidate": "Potential continuity keeper pending further evidence.",
    "promoted": "Strong continuity keeper for long-horizon recall.",
}


_RELATION_OVERRIDES: dict[str, str] = {
    "supports": "Source provides positive support for target.",
    "caused_by": "Source exists because target caused it.",
    "blocked_by": "Source is prevented by target.",
    "unblocks": "Source removes blocking condition for target.",
    "supersedes": "Source replaces target as current preferred assertion/path.",
    "superseded_by": "Source has been replaced by target.",
    "follows": "Temporal or procedural sequence where source follows target.",
    "precedes": "Temporal or procedural sequence where source precedes target.",
    "derived_from": "Source is derived from target via transformation/inference.",
    "contradicts": "Source conflicts with target.",
    "enables": "Source makes target feasible.",
    "associated_with": "Generic association when no stronger relation applies.",
}

RELATION_LABEL_DEFINITIONS: dict[str, str] = {
    label: _RELATION_OVERRIDES.get(
        label,
        f"Canonical semantic relation `{label}`. Use only when evidence supports this specific edge semantics.",
    )
    for label in sorted(AGENT_FACING_RELATION_TYPES)
}


BOUNDARY_RECORD_POLICY_DEFINITIONS: dict[str, str] = {
    "session_start": "System-authored boundary record. Do not treat as normal agent semantic bead type.",
    "session_end": "System-authored boundary record. Do not treat as normal agent semantic bead type.",
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
        "boundary_record_policy": BOUNDARY_RECORD_POLICY_DEFINITIONS,
    }
