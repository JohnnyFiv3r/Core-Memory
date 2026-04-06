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

from .runtime.engine import process_turn_finalized, process_session_start, process_flush, emit_turn_finalized
from .retrieval.tools.memory import search as memory_search, trace as memory_trace, execute as memory_execute
from ._version import VERSION, __version__

from .persistence.store import MemoryStore, DEFAULT_ROOT, DiagnosticError
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

__all__ = [
    # Canonical runtime write boundaries
    "process_turn_finalized",
    "process_session_start",
    "process_flush",
    # Canonical retrieval tool surface
    "memory_search",
    "memory_trace",
    "memory_execute",
    # Adapter/helper ingress surface
    "emit_turn_finalized",
    # Compatibility surface (advanced/legacy/root exports)
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
