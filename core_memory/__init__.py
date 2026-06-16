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
from .memory import (
    Memory,
    approve_bead,
    capture,
    confirm_bead,
    list_pending_approvals,
    reject_bead,
    request_approval,
)
from .transcript_ingest import ingest_transcript, normalize_transcript_payload
from .soul import (
    propose_soul_update,
    approve_soul_update,
    reject_soul_update,
    read_soul_file,
    list_soul_files,
    soul_history,
)
from .runtime.ingest import (
    SourceEventMapping,
    SourceEventRule,
    ingest_document_reference,
    ingest_external_evidence,
    ingest_operational_event,
    ingest_source_event,
    ingest_state_assertion,
    ingest_structured_observation,
)
from .runtime.associations.coverage import (
    apply_association_proposals,
    enqueue_association_coverage,
    get_association_run,
    on_bead_committed,
    run_association_coverage,
)
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
    Grounding,
    ApprovalStatus,
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
    "approve_bead",
    "reject_bead",
    "request_approval",
    "list_pending_approvals",
    "propose_soul_update",
    "approve_soul_update",
    "reject_soul_update",
    "read_soul_file",
    "list_soul_files",
    "soul_history",
    "ingest_transcript",
    "normalize_transcript_payload",
    "ingest_external_evidence",
    "ingest_operational_event",
    "ingest_source_event",
    "SourceEventMapping",
    "SourceEventRule",
    "ingest_state_assertion",
    "ingest_structured_observation",
    "ingest_document_reference",
    "apply_association_proposals",
    "enqueue_association_coverage",
    "get_association_run",
    "on_bead_committed",
    "run_association_coverage",
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
    "Grounding",
    "ApprovalStatus",
    "RelationshipType",
    "ImpactLevel",
    "Association",
    "Event",
]
