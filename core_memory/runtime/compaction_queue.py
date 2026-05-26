"""Backward-compat shim. Canonical location: core_memory.runtime.queue.compaction_queue."""
from core_memory.runtime.queue.compaction_queue import (  # noqa: F401
    process_compaction_event,
    enqueue_compaction_event,
    drain_compaction_queue,
)
