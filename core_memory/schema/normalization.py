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
    "hypothesis",
    "reflection",
    "design_principle",
    "context",
    "transcript",
    "document_reference",
    "structured_observation",
    "state_assertion",
    "data_insight",
    "operational_event",
    "blocked",
    "incident",
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
    "failed_hypothesis": "hypothesis",
    "reversal": "decision",
    "correction": "context",
    "misjudgment": "reflection",
    "overfitted_pattern": "reflection",
    "abandoned_path": "outcome",
    "proposed_theme": "reflection",
}

# Legacy bead type migrations — also set modifier fields when a legacy type is encountered
LEGACY_BEAD_TYPE_MIGRATIONS: dict[str, dict] = {
    "failed_hypothesis": {"type": "hypothesis", "hypothesis_status": "falsified"},
    "reversal":          {"type": "decision",   "revision_type": "reversal"},
    "correction":        {"type": "context",    "revision_type": "correction"},
    "misjudgment":       {"type": "reflection", "reflection_type": "misjudgment"},
    "overfitted_pattern":{"type": "reflection", "reflection_type": "overfitted_pattern"},
    "abandoned_path":    {"type": "outcome",    "result": "abandoned"},
    "proposed_theme":    {"type": "reflection", "reflection_type": "meta_analysis"},
}

# New enum constant sets
HYPOTHESIS_STATUSES = {"pending", "validated", "falsified"}
REFLECTION_TYPES = {"misjudgment", "overfitted_pattern", "meta_analysis", "pattern_recognition"}
OUTCOME_RESULTS = {"resolved", "failed", "partial", "confirmed", "abandoned"}
REVISION_TYPES = {"reversal", "correction"}
INCIDENT_SEVERITIES = {"low", "medium", "high", "critical"}
TOOL_RESULT_STATUSES = {"success", "failure"}
TESTED_BY_VALUES = {"tool", "reasoning", "observation"}

# Canonical assertion kinds for state_assertion beads
ASSERTION_KINDS = {
    "business_state",
    "document_claim",
    "entity_attribute",
    "metric_state",
}

ASSERTION_KIND_ALIASES = {
    "derived_business_state": "business_state",
    "document_observation": "document_claim",
}

# External evidence routing vocabulary. These are the canonical flag sets the
# typed ingest path uses to route external payloads to bead types. They live
# here (not in runtime/ingest) because they are schema vocabulary.
EXTERNAL_TRANSCRIPT_FLAGS = {
    "transcript",
    "conversation.transcript",
    "conversation_transcript",
}
EXTERNAL_DOCUMENT_FLAGS = {
    "document",
    "media",
    "document.media",
    "document_media",
    "document/media",
    "document_reference",
    "media_reference",
}
EXTERNAL_RELATIONAL_FLAGS = {
    "relational",
    "relational.data",
    "relational_data",
    "structured",
    "structured_observation",
    "data_insight",
}
EXTERNAL_STATE_ASSERTION_FLAGS = {
    "state_assertion",
    "derived_business_state",
    "business_state",
    "document_claim",
    "document_observation",
}
# Operational event systems record state transitions of the business
# (GitHub, Jira, Zendesk, HubSpot, PagerDuty, POS/SCADA, ...). A document
# describes reality; a structured table stores reality; an operational
# system records reality changing.
EXTERNAL_OPERATIONAL_FLAGS = {
    "operational",
    "operational_event",
    "operational.event",
    "state_transition",
    "business_event",
}
EXTERNAL_BEAD_TYPES = {
    "transcript",
    "document_reference",
    "structured_observation",
    "state_assertion",
    "data_insight",
    "operational_event",
}

# Confidence classes — truth/governance status, distinct from myelination
# (edge/use strength). C = captured candidate, B = reinforced / used /
# supported, A = canonical / user-confirmed / operationally trusted.
CONFIDENCE_CLASSES = {"C", "B", "A"}

CONFIDENCE_CLASS_ALIASES = {
    "captured": "C",
    "candidate": "C",
    "reinforced": "B",
    "used": "B",
    "supported": "B",
    "canonical": "A",
    "confirmed": "A",
    "trusted": "A",
}

_CONFIDENCE_CLASS_RANK = {"C": 0, "B": 1, "A": 2}

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

# Inference surface canonical relationship set.
# Allow the full canonical structural relation vocabulary so agent-authored
# association judges can express specific semantics directly.
INFERENCE_CANONICAL_RELATION_TYPES = {
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
    "supports",
    "derived_from",
    "resolves",
    "follows",
    "precedes",
    "enables",
}

# Operational statuses (system state, not bead type)
# Note: candidate and promoted are now boolean flags on beads, not status values.
# Legacy beads with status=promoted/candidate are handled via current_promotion_state().
CANONICAL_BEAD_STATUSES = {
    "open",
    "compacted",
    "superseded",
    "archived",
    "transient",
}


def normalize_bead_type(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return ""
    return LEGACY_BEAD_TYPE_ALIASES.get(v, v)


def is_allowed_bead_type(value: str | None) -> bool:
    v = normalize_bead_type(value)
    return bool(v) and v in CANONICAL_BEAD_TYPES


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


# Agent-facing relation types: canonical minus derived helper tags
AGENT_FACING_RELATION_TYPES = CANONICAL_RELATION_TYPES - DERIVED_RELATION_TYPES

# Canonical claim kinds
CANONICAL_CLAIM_KINDS = {
    "preference",
    "identity",
    "policy",
    "commitment",
    "condition",
    "relationship",
    "location",
    "custom",
}

# Claim update decision types
CLAIM_UPDATE_DECISIONS = {
    "reaffirm",
    "supersede",
    "retract",
    "conflict",
}

# Memory interaction roles
MEMORY_INTERACTION_ROLES = {
    "memory_resolution",
    "memory_correction",
    "memory_reflection",
}

# Target lifecycle statuses (future target, does not replace current statuses yet)
TARGET_LIFECYCLE_STATUSES = {
    "default",
    "archived",
    "superseded",
}

# Promotion states (null represented by absence)
PROMOTION_STATES = {
    "candidate",
    "promoted",
}


def normalize_assertion_kind(value: str | None) -> str:
    """Canonical assertion kind; unknown non-empty values are preserved."""
    raw = str(value or "").strip()
    v = raw.lower()
    if not v:
        return "business_state"
    v = ASSERTION_KIND_ALIASES.get(v, v)
    if v in ASSERTION_KINDS:
        return v
    return raw


def normalize_confidence_class(value: str | None, *, default: str = "C") -> str:
    v = str(value or "").strip()
    if not v:
        return default
    upper = v.upper()
    if upper in CONFIDENCE_CLASSES:
        return upper
    return CONFIDENCE_CLASS_ALIASES.get(v.lower(), default)


def confidence_class_rank(value: str | None) -> int:
    return _CONFIDENCE_CLASS_RANK.get(normalize_confidence_class(value), 0)


def derive_confidence_class(bead: dict | None) -> str:
    """Floor confidence class implied by a bead's lifecycle fields.

    Governance status, not retrieval strength: promotion and user confirmation
    grant A; reinforcement signals (recall, candidate marking) grant B.
    """
    b = bead or {}
    authority = str(b.get("authority") or "").strip().lower()
    if bool(b.get("promoted")) or authority == "user_confirmed":
        return "A"
    if str(b.get("status") or "").strip().lower() == "promoted" or str(b.get("promotion_state") or "").strip().lower() == "promoted":
        return "A"
    if bool(b.get("promotion_candidate")) or int(b.get("recall_count") or 0) > 0:
        return "B"
    return "C"


def normalize_claim_kind(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if not v or v not in CANONICAL_CLAIM_KINDS:
        return "custom"
    return v


def normalize_claim_update_decision(value: str | None) -> str:
    v = str(value or "").strip().lower()
    if not v or v not in CLAIM_UPDATE_DECISIONS:
        return "reaffirm"
    return v


def association_policy() -> str:
    return ASSOCIATION_TYPE_POLICY


def normalize_entity_alias(value: str | None) -> str:
    """Canonical entity-alias normalization shared by the registry and projections.

    Lowercase, unify separators, strip punctuation and organization suffixes,
    then remove spaces. Idempotent. This is the single normalization used for
    `entity_aliases` keys — any surface that matches entity strings against
    the registry (e.g. worldline derivation) must use it.
    """
    import re

    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[\s\-_/]+", " ", s)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")
