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

from ._version import VERSION, __version__
from .graph.storylines import derive_storylines
from .graph.worldlines import derive_worldlines, worldline_membership
from .integrations.api import hydrate_bead_sources, write_turn_finalized
from .management import maintain, remove_bead, remove_beads, remove_source, tombstone_bead
from .memory import (
    Memory,
    approve_bead,
    capture,
    confirm_bead,
    list_pending_approvals,
    reject_bead,
    request_approval,
)
from .persistence.backend import JsonFileBackend, SqliteBackend, StorageBackend, create_backend
from .persistence.store import DEFAULT_ROOT, DiagnosticError, MemoryStore
from .retrieval.agent import recall
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
from .retrieval.tools.memory import execute as memory_execute
from .retrieval.tools.memory import search as memory_search
from .retrieval.tools.memory import trace as memory_trace
from .runtime.associations.coverage import (
    apply_association_proposals,
    association_coverage_summary,
    decide_association_candidate,
    enqueue_association_coverage,
    get_association_run,
    list_association_candidates,
    on_bead_committed,
    run_association_coverage,
)
from .runtime.engine import emit_turn_finalized, process_flush, process_session_start, process_turn_finalized
from .runtime.ingest import (
    SourceEventMapping,
    SourceEventRule,
    ingest_chunk_turns,
    ingest_document_reference,
    ingest_external_evidence,
    ingest_operational_event,
    ingest_source_event,
    ingest_state_assertion,
    ingest_structured_observation,
    list_chunk_turns,
)
from .runtime.observability.calibration import compute_calibration_curve
from .runtime.observability.self_model_drift import compute_self_model_drift
from .runtime.observability.tension_meter import compute_tension_resolution_meter
from .schema.models import (
    ApprovalStatus,
    Association,
    Authority,
    Bead,
    BeadType,
    ConfidenceClass,
    Event,
    Grounding,
    ImpactLevel,
    RelationshipType,
    Scope,
    Status,
)
from .schema.turn import Turn
from .soul import (
    abandon_goal,
    apply_soul_update,
    approve_goal,
    approve_soul_update,
    build_soul_summary,
    complete_goal,
    current_soul_entries,
    decay_goal,
    dreamer_soul_findings,
    dreamer_soul_review,
    list_goals,
    list_soul_files,
    propose_goal,
    propose_soul_from_dreamer,
    propose_soul_update,
    read_soul_file,
    reject_goal,
    reject_soul_update,
    soul_history,
    soul_integrity_check,
    soul_integrity_repair,
)
from .transcript_ingest import ingest_transcript, normalize_transcript_payload

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
    "maintain",
    "remove_bead",
    "remove_beads",
    "remove_source",
    "tombstone_bead",
    "propose_soul_update",
    "propose_soul_from_dreamer",
    "dreamer_soul_findings",
    "dreamer_soul_review",
    "approve_soul_update",
    "apply_soul_update",
    "current_soul_entries",
    "reject_soul_update",
    "read_soul_file",
    "list_soul_files",
    "soul_history",
    "soul_integrity_check",
    "soul_integrity_repair",
    "build_soul_summary",
    "propose_goal",
    "approve_goal",
    "reject_goal",
    "complete_goal",
    "abandon_goal",
    "decay_goal",
    "list_goals",
    "ingest_transcript",
    "normalize_transcript_payload",
    "ingest_chunk_turns",
    "ingest_external_evidence",
    "ingest_operational_event",
    "ingest_source_event",
    "SourceEventMapping",
    "SourceEventRule",
    "ingest_state_assertion",
    "ingest_structured_observation",
    "ingest_document_reference",
    "list_chunk_turns",
    "apply_association_proposals",
    "association_coverage_summary",
    "decide_association_candidate",
    "enqueue_association_coverage",
    "get_association_run",
    "list_association_candidates",
    "on_bead_committed",
    "run_association_coverage",
    "recall",
    "recall_result_from_memory_execute",
    "validate_recall_effort",
    # Canonical runtime write boundaries
    "write_turn_finalized",
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
    "__version__",
    "StorageBackend",
    "JsonFileBackend",
    "SqliteBackend",
    "create_backend",
    "hydrate_bead_sources",
    "derive_worldlines",
    "derive_storylines",
    "worldline_membership",
    "compute_calibration_curve",
    "compute_tension_resolution_meter",
    "compute_self_model_drift",
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
