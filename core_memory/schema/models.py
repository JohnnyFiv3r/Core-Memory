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

from .normalization import is_allowed_bead_type, normalize_bead_type, normalize_relation_type, relation_kind


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
    FAILED_HYPOTHESIS = "failed_hypothesis"
    REVERSAL = "reversal"
    MISJUDGMENT = "misjudgment"
    OVERFITTED_PATTERN = "overfitted_pattern"
    ABANDONED_PATH = "abandoned_path"
    REFLECTION = "reflection"
    DESIGN_PRINCIPLE = "design_principle"
    CONTEXT = "context"
    CORRECTION = "correction"


class Scope(str, Enum):
    """Scope of a bead's relevance."""
    PERSONAL = "personal"
    PROJECT = "project"
    GLOBAL = "global"


class Status(str, Enum):
    """Canonical bead status values (aligned to core_memory.schema)."""
    OPEN = "open"
    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    COMPACTED = "compacted"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class Authority(str, Enum):
    """How a bead was created/confirmed."""
    AGENT_INFERRED = "agent_inferred"
    USER_CONFIRMED = "user_confirmed"
    SYSTEM = "system"


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

    raw_type = out.get("type")
    bead_type = normalize_bead_type(raw_type)
    if is_allowed_bead_type(bead_type):
        out["type"] = bead_type
    else:
        raw = str(raw_type).strip() if raw_type is not None else ""
        out["type"] = raw if raw else BeadType.CONTEXT.value

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

    out["confidence"] = _coerce_float_01(out.get("confidence"), default=0.8)
    out["uncertainty"] = _coerce_float_01(out.get("uncertainty"), default=0.5)
    out["recall_count"] = max(0, _coerce_int(out.get("recall_count"), default=0))
    out["retrieval_eligible"] = bool(out.get("retrieval_eligible", False))

    list_fields = [
        "summary",
        "tags",
        "source_turn_ids",
        "retrieval_facts",
        "entities",
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
        "cause_candidates",
        "effect_candidates",
        "supersedes",
        "superseded_by",
    ]
    for key in list_fields:
        if key in out:
            out[key] = _coerce_list(out.get(key))

    if "links" in out:
        out["links"] = _coerce_dict(out.get("links"))
    if "state_change" in out and out.get("state_change") is not None:
        out["state_change"] = _coerce_dict(out.get("state_change"))

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

    # Retrieval richness contract
    retrieval_eligible: bool = False
    retrieval_title: Optional[str] = None
    retrieval_facts: list = field(default_factory=list)
    entities: list = field(default_factory=list)
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

    # Temporal validity / supersession
    observed_at: Optional[str] = None
    recorded_at: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    validity: Optional[str] = None  # open | closed | superseded | transient
    supersedes: list = field(default_factory=list)
    superseded_by: list = field(default_factory=list)

    # Optional enhanced fields
    mechanism: Optional[str] = None
    impact_level: Optional[str] = None
    uncertainty: float = 0.5

    # Contrast fields
    what_almost_happened: Optional[str] = None
    what_was_rejected: Optional[str] = None
    what_felt_risky: Optional[str] = None
    assumption: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return _dataclass_to_dict(self)
    
    def is_retrieval_rich(self) -> bool:
        """True when structured retrieval payload is meaningfully populated."""
        return bool((self.retrieval_title or "").strip()) and bool(self.retrieval_facts)

    def validate_retrieval_eligibility(self) -> bool:
        """Normalize eligibility to match payload quality.

        Thin beads are valid even without summary/associations.
        """
        if not bool(self.retrieval_eligible):
            return True
        quality_signals = any([
            bool(self.because),
            bool(self.supporting_facts),
            bool(self.state_change),
            bool(self.evidence_refs),
            bool(self.supersedes),
            bool(self.superseded_by),
        ])
        ok = self.is_retrieval_rich() and quality_signals
        if not ok:
            self.retrieval_eligible = False
        return ok

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
