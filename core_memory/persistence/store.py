"""
Core-Memory store implementation.

This module contains the MemoryStore class which handles persistence.
Session-first live authority with index projection:
- session JSONL is the live append authority surface
- index.json is a fast projection/cache for retrieval convenience
- events provide audit trail and rebuild capability
"""

from ..persistence.store_contract import (
    BEADS_DIR,
    TURNS_DIR,
    EVENTS_DIR,
    SESSION_FILE,
    INDEX_FILE,
    HEADS_FILE,
    DiagnosticError,
)
from ..persistence.store_core_delegates_mixin import StoreCoreDelegatesMixin
from ..persistence.store_reporting_promotion_mixin import StoreReportingPromotionMixin

# Defaults for pip package (separate from live OpenClaw usage)
DEFAULT_ROOT = "."


# NOTE: durability model
# Archive/event writes happen under a store lock with fsync; index writes are atomic.
# We prefer archive-first for bead persistence so rebuild_index() can recover safely
# from archived JSONL + event logs.


class MemoryStore(StoreReportingPromotionMixin, StoreCoreDelegatesMixin):
    """
    Persistent causal agent memory with lossless compaction.

    Live authority model:
    - session JSONL is authoritative for active-session writes/reads
    - index.json is maintained as projection/cache
    - events are append-only audit/rebuild logs

    Usage:
        memory = MemoryStore(root=".")
        memory.capture_turn(role="assistant", content="...")
        memory.consolidate(session_id="chat-123")
    """

    def __init__(self, root: str = DEFAULT_ROOT, backend: str = "json", tenant_id: str | None = None):
        """Initialize MemoryStore at the given root directory.

        Args:
            root: Root directory for memory storage.
            backend: Storage backend - "json" (default) or "sqlite".
                     Can also be set via CORE_MEMORY_BACKEND env var.
            tenant_id: Optional tenant ID for multi-tenant isolation.
                       Each tenant gets its own subtree under .beads/tenants/{tenant_id}/.
        """
        from ..persistence.store_init_ops import initialize_store_for_store

        initialize_store_for_store(
            self,
            root=root,
            tenant_id=tenant_id,
            backend=backend,
            beads_dir_name=BEADS_DIR,
            turns_dir_name=TURNS_DIR,
        )

    def close(self) -> None:
        from ..persistence.store_lifecycle_ops import close_store_for_store

        close_store_for_store(self)

    def __del__(self):  # pragma: no cover
        from ..persistence.store_lifecycle_ops import safe_del_for_store

        safe_del_for_store(self)
