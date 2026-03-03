"""
Core-Memory: Persistent causal agent memory with lossless compaction.

A structured memory layer for AI agents that preserves causal chains,
compacts intelligently, and scales across sessions.

Architecture: Index-first with event audit log.
- index.json is the primary source of truth (fast queries)
- Events are appended to .beads/events/ for audit/rebuild

Usage:
    from core_memory import MemoryStore, CoreMemory
    memory = MemoryStore(root="./memory")
    memory.capture_turn(...)
"""

from .store import MemoryStore, DEFAULT_ROOT
from .models import (
    Bead,
    BeadType,
    Scope,
    Status,
    Authority,
    RelationshipType,
    ImpactLevel,
    Association,
    Event,
)

__version__ = "1.0.1"

__all__ = [
    "MemoryStore",
    "DEFAULT_ROOT",
    "Bead",
    "BeadType",
    "Scope",
    "Status",
    "Authority",
    "RelationshipType",
    "ImpactLevel",
    "Association",
    "Event",
]
