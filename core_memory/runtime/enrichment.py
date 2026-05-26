"""Backward-compat shim. Canonical location: core_memory.runtime.passes.enrichment."""
from core_memory.runtime.passes.enrichment import (  # noqa: F401
    _enrichment_queue_enabled,
    enqueue_turn_enrichment,
    run_turn_enrichment,
)
