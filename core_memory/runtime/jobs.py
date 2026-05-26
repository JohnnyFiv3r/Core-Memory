"""Backward-compat shim. Canonical location: core_memory.runtime.queue.jobs."""
from core_memory.runtime.queue.jobs import (  # noqa: F401
    async_jobs_status,
    enqueue_async_job,
    run_async_jobs,
    semantic_rebuild_queue_status,
    compaction_queue_status,
    side_effect_queue_status,
)
