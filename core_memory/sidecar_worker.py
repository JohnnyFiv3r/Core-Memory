"""DEPRECATED transitional compatibility shim.

Canonical replacement: `core_memory.event_worker`.

Retained for compatibility only. New runtime-facing code should import
`event_worker`.
"""

from .event_worker import *  # noqa: F401,F403
