"""
Core-Memory data models.

This module contains all type definitions and enums.
"""

import logging
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .normalization import (
    CANONICAL_BEAD_TYPES,
    CANONICAL_CLAIM_KINDS,
    CLAIM_UPDATE_DECISIONS,
    HYPOTHESIS_STATUSES,
    INCIDENT_SEVERITIES,
    LEGACY_BEAD_TYPE_MIGRATIONS,
    OUTCOME_RESULTS,
    REFLECTION_TYPES,
    REVISION_TYPES,
    TESTED_BY_VALUES,
    TOOL_RESULT_STATUSES,
    confidence_class_rank,
    derive_confidence_class,
    is_allowed_bead_type,
    normalize_assertion_kind,
    normalize_bead_type,
    normalize_claim_kind,
    normalize_claim_update_decision,
    normalize_confidence_class,
    normalize_relation_type,
    relation_kind,
)


_LOG = logging.getLogger(__name__)
_UNKNOWN_FIELD_COUNTS: dict[str, Counter[str]] = {}


# === Enums ===

class BeadType(str, Enum):
    """Canonical bead types (aligned to core_memory.schema)."""
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    GOAL = "goal"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    EVIDENCE = "evidence"
    OUTCOME = "outcome"
    LESSON = "lesson"
    CHECKPOINT = "checkpoint"
    PRECEDENT = "precedent"
    HYPOTHESIS = "hypothesis"
    REFLECTION = "reflection"
    DESIGN_PRINCIPLE = "design_principle"
    CONTEXT = "context"
    TRANSCRIPT = "transcript"
    DOCUMENT_REFERENCE = "document_reference"
    STRUCTURED_OBSERVATION = "structured_observation"
    STATE_ASSERTION = "state_assertion"
    DATA_INSIGHT = "data_insight"
    OPERATIONAL_EVENT = "operational_event"
    BLOCKED = "blocked"
    INCIDENT = "incident"


class Scope(str, Enum):
    """Scope of a bead's relevance."""
    PERSONAL = "personal"
    PROJECT = "project"
    GLOBAL = "global"


class Status(str, Enum):
    """Canonical bead status values (aligned to core_memory.schema).

    F-S1: `validity` field is collapsed into `status`. The `transient` value
    absorbs validity=transient. validity=closed maps to ARCHIVED.
    validity=superseded already maps to SUPERSEDED.

    Note: promoted and candidate are now boolean flags (promoted, promotion_candidate)
    on the bead, not status values. Legacy beads with status=promoted/candidate are
    still readable via current_promotion_state() in promotion_contract.py.
    """
    OPEN = "open"
    COMPACTED = "compacted"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    TRANSIENT = "transient"


class Authority(str, Enum):
    """How a bead was created/confirmed."""
    AGENT_INFERRED = "agent_inferred"
    USER_CONFIRMED = "user_confirmed"
    SYSTEM = "system"
    SOURCE_ATTRIBUTED = "source_attributed"
    DERIVED_ANALYSIS = "derived_analysis"


class ConfidenceClass(str, Enum):
    """Truth/governance status of a bead — distinct from myelination
    (edge/use strength). C = captured candidate, B = reinforced/used/supported,
    A = canonical/user-confirmed/operationally trusted."""
    CAPTURED = "C"
    REINFORCED = "B"
    CANONICAL = "A"


class RelationshipType(str, Enum):
    """Canonical relation values (aligned to core_memory.schema)."""
    CAUSED_BY = "caused_by"
    ENABLES = "enables"
    LED_TO = "led_to"
    BLOCKED_BY = "blocked_by"
    UNBLOCKS = "unblocks"
    BLOCKS_UNBLOCKS = "blocks_unblocks"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    ASSOCIATED_WITH = "associated_with"
    CONTRADICTS = "contradicts"
    REFINES = "refines"
    INVALIDATES = "invalidates"
    DIAGNOSES = "diagnoses"
    REINFORCES = "reinforces"
    MIRRORS = "mirrors"
    APPLIES_PATTERN_OF = "applies_pattern_of"
    VIOLATES_PATTERN_OF = "violates_pattern_of"
    CONSTRAINT_TRANSFORMED_INTO = "constraint_transformed_into"
    SOLVES_SAME_MECHANISM = "solves_same_mechanism"
    SIMILAR_PATTERN = "similar_pattern"
    TRANSFERABLE_LESSON = "transferable_lesson"
    GENERALIZES = "generalizes"
    SPECIALIZES = "specializes"
    STRUCTURAL_SYMMETRY = "structural_symmetry"
    REVEALS_BIAS = "reveals_bias"
    SUPPORTS = "supports"
    DERIVED_FROM = "derived_from"
    RESOLVES = "resolves"
    FOLLOWS = "follows"
    PRECEDES = "precedes"


class ImpactLevel(str, Enum):
    """Impact level of a bead."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXISTENTIAL = "existential"


class ClaimKind(str, Enum):
    """Canonical claim kinds for the claim layer."""
    PREFERENCE = "preference"
    IDENTITY = "identity"
    POLICY = "policy"
    COMMITMENT = "commitment"
    CONDITION = "condition"
    RELATIONSHIP = "relationship"
    LOCATION = "location"
    CUSTOM = "custom"


class ClaimUpdateDecision(str, Enum):
    """Decision types for claim updates."""
    REAFFIRM = "reaffirm"
    SUPERSEDE = "supersede"
    RETRACT = "retract"
    CONFLICT = "conflict"


# === Dataclasses ===

def _known_dataclass_kwargs(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls)}
    known: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in (data or {}).items():
        if k in allowed:
            # Defensive copy so mutable payload inputs are not shared by reference.
            known[k] = deepcopy(v)
        else:
            dropped.append(str(k))

    if dropped:
        model_name = getattr(cls, "__name__", str(cls))
        bucket = _UNKNOWN_FIELD_COUNTS.setdefault(model_name, Counter())
        for key in dropped:
            bucket[key] += 1
        _LOG.debug("Dropping unknown %s fields: %s", model_name, sorted(dropped))

    return known


def schema_unknown_field_counters() -> dict[str, dict[str, int]]:
    """Return cumulative counts of unknown fields dropped by model name."""
    return {
        model: dict(counter)
        for model, counter in _UNKNOWN_FIELD_COUNTS.items()
    }


def reset_schema_unknown_field_counters() -> None:
    """Reset dropped-unknown-field counters (primarily for diagnostics/tests)."""
    _UNKNOWN_FIELD_COUNTS.clear()


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Stable dataclass serialization helper.

    We intentionally centralize serializer behavior so future schema additions
    don't require hand-maintained field maps in each model class.
    """
    return asdict(obj)


def _dataclass_from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Create a dataclass instance from known keys only."""
    return cls(**_known_dataclass_kwargs(cls, data))


def _normalize_choice(
    value: Any,
    *,
    allowed: set[str],
    default: str | None = None,
    allow_none: bool = False,
    preserve_unknown: bool = False,
) -> str | None:
    if value is None:
        return None if allow_none else default
    raw = str(value).strip()
    v = raw.lower()
    if not v:
        return None if allow_none else default
    if v in allowed:
        return v
    if preserve_unknown and isinstance(value, str):
        return raw
    return None if allow_none else default


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _coerce_float_01(value: Any, *, default: float) -> float:
    f = _coerce_float(value, default=default)
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _coerce_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _coerce_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def _normalize_bead_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data or {})

    # Apply legacy type migrations BEFORE the existing type normalization
    raw_type_str = str(out.get("type") or "").strip().lower()
    if raw_type_str in LEGACY_BEAD_TYPE_MIGRATIONS:
        migration = LEGACY_BEAD_TYPE_MIGRATIONS[raw_type_str]
        out["type"] = migration["type"]
        for k, v in migration.items():
            if k != "type" and not out.get(k):
                out[k] = v

    raw_type = out.get("type")
    bead_type = normalize_bead_type(raw_type)
    if is_allowed_bead_type(bead_type):
        out["type"] = bead_type
    elif bead_type:  # non-empty but not canonical
        out["type_coerced_from"] = str(out.get("type") or "")
        out["type"] = BeadType.CONTEXT.value
        existing_warnings = list(out.get("validation_warnings") or [])
        if "type:unknown_coerced_to_context" not in existing_warnings:
            existing_warnings.append("type:unknown_coerced_to_context")
        out["validation_warnings"] = existing_warnings
    else:  # empty type
        out["type_coerced_from"] = ""
        out["type"] = BeadType.CONTEXT.value
        existing_warnings = list(out.get("validation_warnings") or [])
        if "type:missing_coerced_to_context" not in existing_warnings:
            existing_warnings.append("type:missing_coerced_to_context")
        out["validation_warnings"] = existing_warnings

    out["scope"] = _normalize_choice(
        out.get("scope"),
        allowed={x.value for x in Scope},
        default=Scope.PROJECT.value,
        preserve_unknown=True,
    )
    out["authority"] = _normalize_choice(
        out.get("authority"),
        allowed={x.value for x in Authority},
        default=Authority.AGENT_INFERRED.value,
        preserve_unknown=True,
    )
    # F-S1: migrate validity → status if status is still default and validity is set
    _validity = str(out.get("validity") or "").strip().lower()
    _status_raw = str(out.get("status") or "").strip().lower()
    if _validity and not _status_raw:
        _validity_to_status = {
            "closed": Status.ARCHIVED.value,
            "superseded": Status.SUPERSEDED.value,
            "transient": Status.TRANSIENT.value,
        }
        if _validity in _validity_to_status:
            out["status"] = _validity_to_status[_validity]

    out["status"] = _normalize_choice(
        out.get("status"),
        allowed={x.value for x in Status},
        default=Status.OPEN.value,
        preserve_unknown=True,
    )
    out["impact_level"] = _normalize_choice(
        out.get("impact_level"),
        allowed={x.value for x in ImpactLevel},
        allow_none=True,
        preserve_unknown=True,
    )

    # Remove validity from output (migration complete)
    out.pop("validity", None)

    out["confidence"] = _coerce_float_01(out.get("confidence"), default=0.8)
    out["uncertainty"] = _coerce_float_01(out.get("uncertainty"), default=0.5)
    out["recall_count"] = max(0, _coerce_int(out.get("recall_count"), default=0))
    out["retrieval_eligible"] = str(out.get("type") or "").strip().lower() in CANONICAL_BEAD_TYPES

    list_fields = [
        "summary",
        "tags",
        "source_turn_ids",
        "retrieval_facts",
        "entities",
        "entity_ids",
        "topics",
        "incident_keys",
        "decision_keys",
        "goal_keys",
        "action_keys",
        "outcome_keys",
        "time_keys",
        "because",
        "supporting_facts",
        "evidence_refs",
        "source_refs",
        "section_refs",
        "entity_refs",
        "attribute_tags",
        "message_refs",
        "speaker_refs",
        "derived_from",
        "derived_from_bead_ids",
        "cause_candidates",
        "effect_candidates",
        "supersedes",
        "superseded_by",
        "tool_output_ids",
        "supports_bead_ids",
    ]
    for key in list_fields:
        if key in out:
            out[key] = _coerce_list(out.get(key))

    if "links" in out:
        out["links"] = _coerce_dict(out.get("links"))
    if "source_attribution" in out and out.get("source_attribution") is not None:
        out["source_attribution"] = _coerce_dict(out.get("source_attribution"))
    if "hydration_ref" in out and out.get("hydration_ref") is not None:
        out["hydration_ref"] = _coerce_dict(out.get("hydration_ref"))
    if "state_change" in out and out.get("state_change") is not None:
        out["state_change"] = _coerce_dict(out.get("state_change"))

    # Claim layer fields
    out["claims"] = _coerce_list(out.get("claims"))
    out["claim_updates"] = _coerce_list(out.get("claim_updates"))
    raw_role = out.get("interaction_role")
    out["interaction_role"] = str(raw_role) if raw_role is not None else None
    raw_outcome = out.get("memory_outcome")
    out["memory_outcome"] = _coerce_dict(raw_outcome) if raw_outcome is not None else None

    # Normalize new enum fields
    if out.get("hypothesis_status"):
        v = str(out["hypothesis_status"]).strip().lower()
        out["hypothesis_status"] = v if v in HYPOTHESIS_STATUSES else None
    if out.get("reflection_type"):
        v = str(out["reflection_type"]).strip().lower()
        out["reflection_type"] = v if v in REFLECTION_TYPES else None
    if out.get("result"):
        v = str(out["result"]).strip().lower()
        out["result"] = v if v in OUTCOME_RESULTS else None
    if out.get("revision_type"):
        v = str(out["revision_type"]).strip().lower()
        out["revision_type"] = v if v in REVISION_TYPES else None
    if out.get("severity"):
        v = str(out["severity"]).strip().lower()
        out["severity"] = v if v in INCIDENT_SEVERITIES else None
    if out.get("tool_result_status"):
        v = str(out["tool_result_status"]).strip().lower()
        out["tool_result_status"] = v if v in TOOL_RESULT_STATUSES else None
    if out.get("tested_by"):
        v = str(out["tested_by"]).strip().lower()
        out["tested_by"] = v if v in TESTED_BY_VALUES else None
    if out.get("assertion_kind"):
        out["assertion_kind"] = normalize_assertion_kind(out.get("assertion_kind"))

    # Confidence class is monotonic: the stored class never reads below the
    # floor implied by lifecycle fields (promoted / user_confirmed / recalled).
    provided_class = normalize_confidence_class(out.get("confidence_class"))
    derived_class = derive_confidence_class(out)
    out["confidence_class"] = (
        provided_class
        if confidence_class_rank(provided_class) >= confidence_class_rank(derived_class)
        else derived_class
    )

    return out


def _normalize_association_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data or {})
    raw_rel = out.get("relationship")
    rel = normalize_relation_type(raw_rel)
    if relation_kind(rel) == "canonical":
        out["relationship"] = rel
    else:
        raw = str(raw_rel).strip() if raw_rel is not None else ""
        out["relationship"] = raw if raw else RelationshipType.ASSOCIATED_WITH.value
    out["novelty"] = _coerce_float_01(out.get("novelty"), default=0.5)
    out["confidence"] = _coerce_float_01(out.get("confidence"), default=0.5)
    out["decay_score"] = max(0.0, _coerce_float(out.get("decay_score"), default=1.0))
    out["reinforced_count"] = max(0, _coerce_int(out.get("reinforced_count"), default=0))
    return out


def _normalize_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data or {})
    if "payload" not in out:
        out["payload"] = {}
    else:
        out["payload"] = deepcopy(out.get("payload"))
    return out


def _normalize_claim_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data or {})
    for k in ("subject", "slot", "reason_text", "observed_at", "recorded_at", "effective_from", "effective_to"):
        if out.get(k) is not None:
            out[k] = str(out.get(k))
    out["claim_kind"] = normalize_claim_kind(out.get("claim_kind"))
    out["confidence"] = _coerce_float_01(out.get("confidence"), default=0.8)
    return out


def _normalize_claim_update_payload(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data or {})
    # Canonical claim-update keys
    if not out.get("target_claim_id") and out.get("claim_id"):
        out["target_claim_id"] = out.get("claim_id")
    if not out.get("replacement_claim_id") and out.get("successor_claim_id"):
        out["replacement_claim_id"] = out.get("successor_claim_id")

    # Normalize optional string fields
    for k in (
        "target_claim_id",
        "replacement_claim_id",
        "subject",
        "slot",
        "reason_text",
        "trigger_bead_id",
        "grounding_hash",
        "judge_model",
        "prompt_version",
        "rubric_version",
    ):
        if out.get(k) is not None:
            out[k] = str(out.get(k))
    if out.get("chain_seq") is not None:
        try:
            out["chain_seq"] = int(out.get("chain_seq"))
        except Exception:
            out.pop("chain_seq", None)
    if out.get("evidence_bead_ids") is not None:
        out["evidence_bead_ids"] = [str(x) for x in (out.get("evidence_bead_ids") or []) if str(x).strip()]

    out["decision"] = normalize_claim_update_decision(out.get("decision"))
    out["confidence"] = _coerce_float_01(out.get("confidence"), default=0.8)
    return out


@dataclass
class SpeakerAttribution:
    """Observed speaker label resolved to a canonical entity."""

    speaker_observed: str
    resolved_entity_id: str | None
    resolution_confidence: float
    source_system: str
    aliases: list = field(default_factory=list)
    resolved: bool = False


@dataclass
class Claim:
    """A claim captures a discrete user-stated or agent-inferred fact."""
    id: str = ""
    claim_kind: str = "custom"
    subject: str = ""
    slot: str = ""
    value: Any = None
    reason_text: str = ""
    confidence: float = 0.8
    observed_at: str | None = None
    recorded_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    context_scope: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Claim":
        """Create from dictionary, ignoring unknown keys."""
        return _dataclass_from_dict(cls, _normalize_claim_payload(data))


@dataclass
class ClaimUpdate:
    """A claim update records a decision about an existing claim."""
    id: str = ""
    decision: str = "reaffirm"
    target_claim_id: str = ""
    replacement_claim_id: str | None = None
    subject: str = ""
    slot: str = ""
    reason_text: str = ""
    confidence: float = 0.8
    trigger_bead_id: str | None = None
    grounding_hash: str = ""
    evidence_bead_ids: list = field(default_factory=list)
    judge_model: str = "current-runtime"
    prompt_version: str = "current-runtime"
    rubric_version: str = "current-runtime"
    chain_seq: int | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ClaimUpdate":
        """Create from dictionary, ignoring unknown keys."""
        return _dataclass_from_dict(cls, _normalize_claim_update_payload(data))


@dataclass
class Bead:
    """A bead is the canonical record for one turn.

    Thin vs rich is determined by field completeness and retrieval_eligible,
    not by bead type.
    """
    id: str
    type: str  # BeadType as string for JSON compatibility
    title: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: Optional[str] = None
    summary: list = field(default_factory=list)  # optional by contract
    detail: str = ""
    scope: str = "project"  # Scope as string
    authority: str = "agent_inferred"  # Authority as string
    confidence: float = 0.8
    tags: list = field(default_factory=list)
    links: dict = field(default_factory=dict)
    status: str = "open"  # Status as string
    recall_count: int = 0
    last_recalled: Optional[str] = None

    # Core turn grounding
    source_turn_ids: list = field(default_factory=list)
    turn_index: Optional[int] = None
    prev_bead_id: Optional[str] = None
    next_bead_id: Optional[str] = None

    # Type progression (append-only post-write)
    type_log: list = field(default_factory=list)
    type_coerced_from: Optional[str] = None

    # Retrieval richness contract
    retrieval_eligible: bool = False
    retrieval_title: Optional[str] = None
    retrieval_facts: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    entity_ids: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    incident_keys: list = field(default_factory=list)
    decision_keys: list = field(default_factory=list)
    goal_keys: list = field(default_factory=list)
    action_keys: list = field(default_factory=list)
    outcome_keys: list = field(default_factory=list)
    time_keys: list = field(default_factory=list)

    # Reasoning/evidence payload
    because: list = field(default_factory=list)
    supporting_facts: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    cause_candidates: list = field(default_factory=list)
    effect_candidates: list = field(default_factory=list)
    state_change: Optional[dict] = None

    # External source attribution / hydration handles.
    # These are semantic pointers, not raw source replicas.
    data_type_flag: Optional[str] = None
    source_id: Optional[str] = None
    source_event_id: Optional[str] = None
    source_system: Optional[str] = None
    source_kind: Optional[str] = None
    source_ref: Optional[str] = None
    source_refs: list = field(default_factory=list)
    source_attribution: Optional[dict] = None
    core_memory_unifying_id: Optional[str] = None
    hydration_ref: Optional[dict] = None
    derived_from: list = field(default_factory=list)
    derived_from_bead_ids: list = field(default_factory=list)

    # Temporal validity / supersession
    observed_at: Optional[str] = None
    recorded_at: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    validity: Optional[str] = None  # DEPRECATED (F-S1): use status instead. Retained for migration.
    supersedes: list = field(default_factory=list)
    superseded_by: list = field(default_factory=list)

    # Promotion flags (monotonically true once set — never removed)
    promoted: bool = False
    promotion_candidate: bool = False
    promoted_at: Optional[str] = None
    promotion_locked: bool = False
    promotion_score: Optional[float] = None
    promotion_threshold: Optional[float] = None
    promotion_reason: Optional[str] = None

    # Optional enhanced fields
    mechanism: Optional[str] = None
    impact_level: Optional[str] = None
    uncertainty: float = 0.5

    # Truth/governance status (C/B/A) — distinct from myelination
    confidence_class: str = "C"

    # Contrast fields
    what_almost_happened: Optional[str] = None
    what_was_rejected: Optional[str] = None
    what_felt_risky: Optional[str] = None
    assumption: Optional[str] = None

    # Claim layer fields
    claims: list = field(default_factory=list)
    claim_updates: list = field(default_factory=list)
    interaction_role: Optional[str] = None
    memory_outcome: Optional[dict] = None

    # Cross-bead incident linking
    incident_id: Optional[str] = None

    # Revision modifier (any non-system type)
    revises_bead_id: Optional[str] = None
    revision_type: Optional[str] = None   # reversal | correction

    # Write-path diagnostics
    validation_warnings: list = field(default_factory=list)
    decision_conflict_with: list = field(default_factory=list)
    unjustified_flip: bool = False

    # Speaker attribution
    speaker_attribution: Optional[dict] = None
    attributed_entity_id: Optional[str] = None
    resolution_confidence: Optional[float] = None

    # Misc
    context_tags: list = field(default_factory=list)

    # goal
    goal_id: Optional[str] = None
    success_criteria: Optional[str] = None

    # outcome
    result: Optional[str] = None
    linked_bead_id: Optional[str] = None

    # evidence
    supports_bead_ids: list = field(default_factory=list)

    # transcript/reference family
    transcript_id: Optional[str] = None
    conversation_id: Optional[str] = None
    source_thread_id: Optional[str] = None
    source_session_id: Optional[str] = None
    message_refs: list = field(default_factory=list)
    speaker_refs: list = field(default_factory=list)

    # document/media family
    document_id: Optional[str] = None
    raw_source_object_id: Optional[str] = None
    ragie_document_id: Optional[str] = None
    document_name: Optional[str] = None
    mime_type: Optional[str] = None
    document_kind: Optional[str] = None
    document_date: Optional[str] = None
    author_or_owner: Optional[str] = None
    section_refs: list = field(default_factory=list)

    # operational event family (state transitions of the business).
    # Transitions ACCUMULATE — sibling events of the same business object
    # coexist as history; only derived state (state_assertion) supersedes.
    actor: Optional[str] = None

    # relational/structured family
    source_table: Optional[str] = None
    source_record_id: Optional[str] = None
    record_action: Optional[str] = None
    record_grain: Optional[str] = None
    business_object_type: Optional[str] = None
    business_object_id: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_unit: Optional[str] = None
    change_pct: Optional[float] = None
    currency: Optional[str] = None
    as_of_timestamp: Optional[str] = None
    entity_refs: list = field(default_factory=list)
    attribute_tags: list = field(default_factory=list)

    # interpreted/derived state family
    assertion_kind: Optional[str] = None
    assertion_subject: Optional[str] = None
    assertion_predicate: Optional[str] = None
    assertion_value: Optional[str] = None

    # precedent
    condition: Optional[str] = None
    action: Optional[str] = None

    # hypothesis
    hypothesis_status: Optional[str] = None
    tested_by: Optional[str] = None
    failure_signature: Optional[str] = None

    # reflection
    reflection_type: Optional[str] = None

    # tool_call
    tool: Optional[str] = None
    capability: Optional[str] = None
    tool_result_status: Optional[str] = None
    tool_output_id: Optional[str] = None
    tool_output_ids: list = field(default_factory=list)

    # decision / design_principle / goal (write-path computed)
    constraints: list = field(default_factory=list)

    # blocked
    blocked_by_description: Optional[str] = None
    blocking_bead_id: Optional[str] = None

    # incident
    severity: Optional[str] = None
    resolved_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)
    
    def is_retrieval_rich(self) -> bool:
        """True when structured retrieval payload is meaningfully populated."""
        return bool((self.retrieval_title or "").strip()) and bool(self.retrieval_facts)

    def validate_retrieval_eligibility(self) -> bool:
        """Normalize eligibility: eligible iff the bead type is recognized."""
        eligible = str(self.type or "").strip().lower() in CANONICAL_BEAD_TYPES
        self.retrieval_eligible = eligible
        return eligible

    @classmethod
    def from_dict(cls, data: dict) -> "Bead":
        """Create from dictionary, ignoring unknown keys."""
        obj = _dataclass_from_dict(cls, _normalize_bead_payload(data))
        obj.validate_retrieval_eligibility()
        return obj


@dataclass
class Association:
    """An association links two beads together."""
    id: str
    source_bead: str
    target_bead: str
    relationship: str  # RelationshipType as string
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    explanation: str = ""
    novelty: float = 0.5
    confidence: float = 0.5
    reinforced_count: int = 0
    decay_score: float = 1.0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Association":
        """Create from dictionary, ignoring unknown keys."""
        return _dataclass_from_dict(cls, _normalize_association_payload(data))


@dataclass
class Event:
    """An event represents a state change in the memory system."""
    id: str
    event_type: str
    session_id: Optional[str]
    payload: dict
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        """Create from dictionary, ignoring unknown keys."""
        return _dataclass_from_dict(cls, _normalize_event_payload(data))
