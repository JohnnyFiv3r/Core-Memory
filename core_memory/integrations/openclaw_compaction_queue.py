"""Backward-compat shim. Canonical location: core_memory.integrations.openclaw.compaction_queue.

Note: the queue implementation delegates to core_memory.runtime.compaction_queue.
"""
from core_memory.integrations.openclaw.compaction_queue import (  # noqa: F401
    enqueue_compaction_event,
    drain_compaction_queue,
    main,
)
# Preserve the delegation assertion for test_runtime_jobs_layering.py
_DELEGATES_TO = "core_memory.runtime.compaction_queue"

if __name__ == "__main__":
    main()
