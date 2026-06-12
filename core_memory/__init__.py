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
from .memory import Memory, capture, confirm_bead
from .transcript_ingest import ingest_transcript, normalize_transcript_payload
from .runtime.ingest import ingest_document_reference, ingest_external_evidence, ingest_state_assertion, ingest_structured_observation
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
from .graph.worldlines import derive_worldlines, worldline_membership
from .graph.storylines import derive_storylines
from ._version import VERSION, __version__

from .persistence.store import MemoryStore, DEFAULT_ROOT, DiagnosticError
from .persistence.backend import StorageBackend, JsonFileBackend, SqliteBackend, create_backend
from .schema.models import (
    Bead,
    BeadType,
    Scope,
    Status,
    Authority,
    ConfidenceClass,
    RelationshipType,
    ImpactLevel,
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
    "confirm_bead",
    "ingest_transcript",
    "normalize_transcript_payload",
    "ingest_external_evidence",
    "ingest_state_assertion",
    "ingest_structured_observation",
    "ingest_document_reference",
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
    "derive_worldlines",
    "derive_storylines",
    "worldline_membership",
    "Bead",
    "BeadType",
    "Scope",
    "Status",
    "Authority",
    "ConfidenceClass",
    "RelationshipType",
    "ImpactLevel",
    "Association",
    "Event",
]
