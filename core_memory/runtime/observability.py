"""Backward-compat shim. Canonical location: core_memory.runtime.observability.observability."""
from core_memory.runtime.observability.observability import (  # noqa: F401
    emit_event,
    increment,
    record_timing,
    get_metrics,
    Timer,
)
