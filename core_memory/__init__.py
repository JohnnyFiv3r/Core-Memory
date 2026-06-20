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
from .management import maintain, remove_bead, remove_beads, remove_source
from .transcript_ingest import ingest_transcript, normalize_transcript_payload
from .soul import (
    propose_soul_update,
    propose_soul_from_dreamer,
    dreamer_soul_findings,
    dreamer_soul_review,
    approve_soul_update,
    apply_soul_update,
    reject_soul_update,
    read_soul_file,
    list_soul_files,
    soul_history,
    soul_integrity_check,
    soul_integrity_repair,
    build_soul_summary,
    propose_goal,
    approve_goal,
    reject_goal,
    complete_goal,
    abandon_goal,
    decay_goal,
    list_goals,
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
    association_coverage_summary,
    decide_association_candidate,
    enqueue_association_coverage,
    get_association_run,
    list_association_candidates,
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
from .runtime.observability.calibration import compute_calibration_curve
from .runtime.observability.tension_meter import compute_tension_resolution_meter
from .runtime.observability.self_model_drift import compute_self_model_drift
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
    "maintain",
    "remove_bead",
    "remove_beads",
    "remove_source",
    "propose_soul_update",
    "propose_soul_from_dreamer",
    "dreamer_soul_findings",
    "dreamer_soul_review",
    "approve_soul_update",
    "apply_soul_update",
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
    "ingest_external_evidence",
    "ingest_operational_event",
    "ingest_source_event",
    "SourceEventMapping",
    "SourceEventRule",
    "ingest_state_assertion",
    "ingest_structured_observation",
    "ingest_document_reference",
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
