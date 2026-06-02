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
from .retrieval.agent import recall
from .memory import Memory, capture
from .transcript_ingest import ingest_transcript, normalize_transcript_payload
from .schema.turn import Turn
from .retrieval.contracts import (
    ClaimSlotItem,
    ConflictItem,
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    ResolvedGoalItem,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)
from .integrations.api import hydrate_bead_sources
from ._version import VERSION, __version__

from .persistence.store import MemoryStore, DEFAULT_ROOT, DiagnosticError
from .persistence.backend import StorageBackend, JsonFileBackend, SqliteBackend, create_backend
from .schema.models import (
    Bead,
    BeadType,
    Scope,
    Status,
    RelationshipType,
    Association,
    Event,
)

__all__ = [
    # Friendly quick-start aliases
    "Memory",
    "Turn",
    "ClaimSlotItem",
    "ConflictItem",
    "EvidenceItem",
    "ResolvedGoalItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "capture",
    "ingest_transcript",
    "normalize_transcript_payload",
    "recall",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
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
    "hydrate_bead_sources",
    "Bead",
    "BeadType",
    "Scope",
    "Status",
    "RelationshipType",
    "Association",
    "Event",
]
