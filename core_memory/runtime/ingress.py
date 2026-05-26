"""Backward-compat shim. Canonical location: core_memory.runtime.turn.ingress."""
from core_memory.runtime.turn.ingress import (  # noqa: F401
    should_emit_memory_event,
    maybe_emit_finalize_memory_event,
)
