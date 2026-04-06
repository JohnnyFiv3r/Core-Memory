"""
Core-Memory data models.

This module contains all type definitions and enums.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any, Optional


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
    LED_TO = "led_to"
    BLOCKED_BY = "blocked_by"
    UNBLOCKS = "unblocks"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    ASSOCIATED_WITH = "associated_with"
    CONTRADICTS = "contradicts"
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


class ImpactLevel(str, Enum):
    """Impact level of a bead."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXISTENTIAL = "existential"


# === Dataclasses ===

def _known_dataclass_kwargs(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in allowed}


@dataclass
class Bead:
    """A bead is the canonical record for one turn.

    Thin vs rich is determined by field completeness and retrieval_eligible,
    not by bead type.
    """
    id: str
    type: str  # BeadType as string for JSON compatibility
    title: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
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
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "created_at": self.created_at,
            "session_id": self.session_id,
            "summary": self.summary,
            "detail": self.detail,
            "scope": self.scope,
            "authority": self.authority,
            "confidence": self.confidence,
            "tags": self.tags,
            "links": self.links,
            "status": self.status,
            "recall_count": self.recall_count,
            "last_recalled": self.last_recalled,
            "source_turn_ids": self.source_turn_ids,
            "turn_index": self.turn_index,
            "prev_bead_id": self.prev_bead_id,
            "next_bead_id": self.next_bead_id,
            "retrieval_eligible": self.retrieval_eligible,
            "retrieval_title": self.retrieval_title,
            "retrieval_facts": self.retrieval_facts,
            "entities": self.entities,
            "topics": self.topics,
            "incident_keys": self.incident_keys,
            "decision_keys": self.decision_keys,
            "goal_keys": self.goal_keys,
            "action_keys": self.action_keys,
            "outcome_keys": self.outcome_keys,
            "time_keys": self.time_keys,
            "because": self.because,
            "supporting_facts": self.supporting_facts,
            "evidence_refs": self.evidence_refs,
            "cause_candidates": self.cause_candidates,
            "effect_candidates": self.effect_candidates,
            "state_change": self.state_change,
            "observed_at": self.observed_at,
            "recorded_at": self.recorded_at,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "validity": self.validity,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "mechanism": self.mechanism,
            "impact_level": self.impact_level,
            "uncertainty": self.uncertainty,
            "what_almost_happened": self.what_almost_happened,
            "what_was_rejected": self.what_was_rejected,
            "what_felt_risky": self.what_felt_risky,
            "assumption": self.assumption,
        }
    
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
        obj = cls(**_known_dataclass_kwargs(cls, data))
        obj.validate_retrieval_eligibility()
        return obj


@dataclass
class Association:
    """An association links two beads together."""
    id: str
    source_bead: str
    target_bead: str
    relationship: str  # RelationshipType as string
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    explanation: str = ""
    novelty: float = 0.5
    confidence: float = 0.5
    reinforced_count: int = 0
    decay_score: float = 1.0
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "source_bead": self.source_bead,
            "target_bead": self.target_bead,
            "relationship": self.relationship,
            "created_at": self.created_at,
            "explanation": self.explanation,
            "novelty": self.novelty,
            "confidence": self.confidence,
            "reinforced_count": self.reinforced_count,
            "decay_score": self.decay_score,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Association":
        """Create from dictionary, ignoring unknown keys."""
        return cls(**_known_dataclass_kwargs(cls, data))


@dataclass
class Event:
    """An event represents a state change in the memory system."""
    id: str
    event_type: str
    session_id: Optional[str]
    payload: dict
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        """Create from dictionary, ignoring unknown keys."""
        return cls(**_known_dataclass_kwargs(cls, data))
