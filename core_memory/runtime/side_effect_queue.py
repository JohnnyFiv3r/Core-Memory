"""Backward-compat shim. Canonical location: core_memory.runtime.queue.side_effect_queue."""
from core_memory.runtime.queue.side_effect_queue import (  # noqa: F401
    _SIDE_EFFECT_KINDS,
    _queue_path,
    enqueue_side_effect_event,
    side_effect_queue_status,
    drain_side_effect_queue,
    process_side_effect_event,
)
