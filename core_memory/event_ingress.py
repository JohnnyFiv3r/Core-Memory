"""Canonical event ingress surface (V2P13).

Compatibility note:
- Backed by legacy `core_memory.sidecar_hook` implementation during transition.
"""

from .sidecar_hook import (  # noqa: F401
    should_emit_memory_event,
    maybe_emit_finalize_memory_event,
)

__all__ = [
    "should_emit_memory_event",
    "maybe_emit_finalize_memory_event",
]
