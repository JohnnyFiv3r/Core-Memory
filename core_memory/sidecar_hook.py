"""DEPRECATED transitional compatibility shim.

Canonical replacement: `core_memory.event_ingress`.

Retained for compatibility only. New runtime-facing code should import
`event_ingress`.
"""

from .event_ingress import *  # noqa: F401,F403
