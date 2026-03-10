"""DEPRECATED transitional compatibility shim.

Canonical replacement: `core_memory.event_state`.

Retained for compatibility only. New runtime-facing code should import
`event_state`.
"""

from .event_state import *  # noqa: F401,F403
