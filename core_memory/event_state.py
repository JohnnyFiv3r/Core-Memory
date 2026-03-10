"""Canonical event-state surface (V2P13).

Compatibility note:
- Backed by legacy `core_memory.sidecar` implementation during transition.
"""

from .sidecar import (  # noqa: F401
    TurnEnvelope,
    MemoryEvent,
    memory_pass_key,
    sha256_hex,
    emit_memory_event,
    mark_memory_pass,
    get_memory_pass,
    try_claim_memory_pass,
)

__all__ = [
    "TurnEnvelope",
    "MemoryEvent",
    "memory_pass_key",
    "sha256_hex",
    "emit_memory_event",
    "mark_memory_pass",
    "get_memory_pass",
    "try_claim_memory_pass",
]
