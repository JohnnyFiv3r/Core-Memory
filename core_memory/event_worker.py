"""Canonical event worker surface (V2P13).

Compatibility note:
- Backed by legacy `core_memory.sidecar_worker` implementation during transition.
"""

from .sidecar_worker import (  # noqa: F401
    SidecarPolicy,
    process_memory_event,
)

__all__ = [
    "SidecarPolicy",
    "process_memory_event",
]
