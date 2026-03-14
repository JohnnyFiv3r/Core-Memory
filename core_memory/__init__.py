"""
Core-Memory: event/session-first memory layer for agent runtimes.

High-level architecture:
- finalized-turn event ingestion is canonical write ingress
- live session authority is session JSONL surface
- index/projection surfaces are rebuildable caches/projections

Canonical contributor docs:
- docs/architecture_overview.md
- docs/public_surface.md
"""

from .persistence.store import MemoryStore, DEFAULT_ROOT
from .schema.models import (
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
