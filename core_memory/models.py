"""
Core-Memory data models.

This module contains all type definitions and enums.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# === Enums ===

class BeadType(Enum):
    """Types of beads in the memory system."""
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
    ASSOCIATION = "association"
    FAILED_HYPOTHESIS = "failed_hypothesis"
    REVERSAL = "reversal"
    MISJUDGMENT = "misjudgment"
    OVERFITTED_PATTERN = "overfitted_pattern"
    ABANDONED_PATH = "abandoned_path"
    REFLECTION = "reflection"
    DESIGN_PRINCIPLE = "design_principle"


class Scope(Enum):
    """Scope of a bead's relevance."""
    PERSONAL = "personal"
    PROJECT = "project"
    GLOBAL = "global"


class Status(Enum):
    """Status of a bead in the lifecycle."""
    OPEN = "open"
    CLOSED = "closed"
    PROMOTED = "promoted"
    COMPACTED = "compacted"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class Authority(Enum):
    """How a bead was created/confirmed."""
    AGENT_INFERRED = "agent_inferred"
    USER_CONFIRMED = "user_confirmed"
    SYSTEM = "system"


class RelationshipType(Enum):
    """Types of relationships between beads."""
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
    TRANSFERRABLE_LESSON = "transferable_lesson"
    GENERALIZES = "generalizes"
    SPECIALIZES = "specializes"
    STRUCTURAL_SYMMETRY = "structural_symmetry"
    REVEALS_BIAS = "reveals_bias"


class ImpactLevel(Enum):
    """Impact level of a bead."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXISTENTIAL = "existential"


# === Dataclasses ===

@dataclass
class Bead:
    """A bead represents a discrete unit of memory."""
    id: str
    type: str  # BeadType as string for JSON compatibility
    title: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    session_id: Optional[str] = None
    summary: list = field(default_factory=list)
    detail: str = ""
    scope: str = "project"  # Scope as string
    authority: str = "agent_inferred"  # Authority as string
    confidence: float = 0.8
    tags: list = field(default_factory=list)
    links: dict = field(default_factory=dict)
    status: str = "open"  # Status as string
    recall_count: int = 0
    last_recalled: Optional[str] = None
    
    # Optional enhanced fields
    because: list = field(default_factory=list)
    source_turn_ids: list = field(default_factory=list)
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
            "because": self.because,
            "source_turn_ids": self.source_turn_ids,
            "mechanism": self.mechanism,
            "impact_level": self.impact_level,
            "uncertainty": self.uncertainty,
            "what_almost_happened": self.what_almost_happened,
            "what_was_rejected": self.what_was_rejected,
            "what_felt_risky": self.what_felt_risky,
            "assumption": self.assumption,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Bead":
        """Create from dictionary."""
        return cls(**data)


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
        """Create from dictionary."""
        return cls(**data)


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
        """Create from dictionary."""
        return cls(**data)
