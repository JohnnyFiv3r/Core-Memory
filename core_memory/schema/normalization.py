from __future__ import annotations

"""Canonical schema vocabulary + normalization helpers.

Phase T1 intent:
- Separate bead types, edge relationships, and operational states/statuses.
- Preserve legacy input compatibility via explicit normalization.
"""

# Association type policy (P7 confirmed decision)
# edge_primary_no_association_bead:
# - associations are modeled as a separate class (edges/association records)
# - association is not a canonical bead type
ASSOCIATION_TYPE_POLICY = "edge_primary_no_association_bead"


# Canonical bead types
CANONICAL_BEAD_TYPES = {
    "session_start",
    "session_end",
    "goal",
    "decision",
    "tool_call",
    "evidence",
    "outcome",
    "lesson",
    "checkpoint",
    "precedent",
    "failed_hypothesis",
    "reversal",
    "misjudgment",
    "overfitted_pattern",
    "abandoned_path",
    "reflection",
    "design_principle",
    "context",
    "correction",
}

# Boundary bead types are valid canonical records but are not part of the
# public retrieval facet catalog by default.
BOUNDARY_BEAD_TYPES = {
    "session_start",
    "session_end",
}

PUBLIC_CATALOG_BEAD_TYPES = {
    bt for bt in CANONICAL_BEAD_TYPES if bt not in BOUNDARY_BEAD_TYPES
}

# Legacy bead aliases -> canonical bead type
LEGACY_BEAD_TYPE_ALIASES = {
    "promoted_lesson": "lesson",
    "promoted_decision": "decision",
}

# Canonical structural/semantic relation types
CANONICAL_RELATION_TYPES = {
    "caused_by",
    "led_to",
    "blocked_by",
    "unblocks",
    "blocks_unblocks",
    "supersedes",
    "superseded_by",
    "associated_with",
    "contradicts",
    "refines",
    "invalidates",
    "diagnoses",
    "reinforces",
    "mirrors",
    "applies_pattern_of",
    "violates_pattern_of",
    "constraint_transformed_into",
    "solves_same_mechanism",
    "similar_pattern",
    "transferable_lesson",
    "generalizes",
    "specializes",
    "structural_symmetry",
    "reveals_bias",
    # observed additional canonicalized relations
    "supports",
    "derived_from",
    "resolves",
    "follows",
    "precedes",
    "enables",
}

# Derived/helper relation tags (not canonical structural edges)
DERIVED_RELATION_TYPES = {
    "related",
    "shared_tag",
}

# Legacy relation aliases -> canonical relation vocabulary.
RELATION_TYPE_ALIASES = {
    "causes": "caused_by",
    "blocks→unblocks": "blocks_unblocks",
    "blocks->unblocks": "blocks_unblocks",
}

# Inference surface canonical relationship set (v2.1).
INFERENCE_CANONICAL_RELATION_TYPES = {
    "caused_by",
    "supports",
    "supersedes",
    "blocked_by",
    "unblocks",
    "enables",
    "derived_from",
    "follows",
    "contradicts",
}

# Operational statuses (system state, not bead type)
CANONICAL_BEAD_STATUSES = {
    "default",
    "archived",
    "superseded",
}


def normalize_bead_type(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return "context"
    return LEGACY_BEAD_TYPE_ALIASES.get(v, v)


def is_allowed_bead_type(value: str | None) -> bool:
    return normalize_bead_type(value) in CANONICAL_BEAD_TYPES


def normalize_relation_type(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return "associated_with"
    return RELATION_TYPE_ALIASES.get(v, v)


def relation_kind(value: str | None) -> str:
    r = normalize_relation_type(value)
    if r in CANONICAL_RELATION_TYPES:
        return "canonical"
    if r in DERIVED_RELATION_TYPES:
        return "derived"
    return "unknown"


def association_policy() -> str:
    return ASSOCIATION_TYPE_POLICY
