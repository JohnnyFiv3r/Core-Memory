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
from .memory import Memory, capture
from .schema.turn import Turn
from .retrieval.contracts import (
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)
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

_RECALL_BUDGETS = {"cheap", "default", "full"}


def recall(
    query_or_request: str | dict,
    *,
    budget: str = "default",
    intent: str | None = None,
    k: int | None = None,
    speaker: str | None = None,
    root: str = ".",
    explain: bool = True,
    **request_overrides,
) -> dict:
    """Friendly single-verb read wrapper over `memory_execute(...)`.

    String input is normalized into a `memory_execute` request with
    `intent="remember"` and `k=8` defaults. Dict input is treated as an
    already-shaped request and only receives missing defaults.

    `budget` and `speaker` are accepted as public recall parameters and passed
    through in the request payload; this wrapper does not interpret or enforce
    them beyond validating budget values.
    """
    if budget not in _RECALL_BUDGETS:
        allowed = ", ".join(sorted(_RECALL_BUDGETS))
        raise ValueError(f"budget must be one of: {allowed}")

    if isinstance(query_or_request, str):
        raw_query = query_or_request.strip()
        if not raw_query:
            raise ValueError("recall query must be a non-empty string")
        request = {
            "raw_query": raw_query,
            "intent": intent or "remember",
            "k": 8 if k is None else int(k),
            "budget": budget,
        }
    elif isinstance(query_or_request, dict):
        request = dict(query_or_request)
        request.setdefault("intent", intent or "remember")
        request.setdefault("k", 8 if k is None else int(k))
        request.setdefault("budget", budget)
    else:
        raise TypeError("recall query_or_request must be a string or dict")

    if speaker is not None:
        request.setdefault("speaker", speaker)
    request.update(request_overrides)
    return memory_execute(request=request, root=root, explain=explain)


__all__ = [
    # Friendly quick-start aliases
    "Memory",
    "Turn",
    "EvidenceItem",
    "SourceItem",
    "RecallPlanning",
    "RecallResult",
    "RecallStep",
    "capture",
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
