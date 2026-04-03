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

from .persistence.store import MemoryStore, DEFAULT_ROOT, DiagnosticError, VERSION
from .persistence.backend import StorageBackend, JsonFileBackend, SqliteBackend, create_backend
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

__version__ = VERSION

__all__ = [
    "MemoryStore",
    "DEFAULT_ROOT",
    "DiagnosticError",
    "VERSION",
    "StorageBackend",
    "JsonFileBackend",
    "SqliteBackend",
    "create_backend",
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
