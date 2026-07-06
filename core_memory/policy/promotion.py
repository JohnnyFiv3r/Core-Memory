"""Public promotion-policy import surface.

The pure stored-bead promotion rules live in ``core_memory.schema.promotion`` so
lower layers can use them without importing upward into policy.
"""
from __future__ import annotations

from core_memory.schema.promotion import *  # noqa: F401,F403
from core_memory.schema.promotion import _days_since_last_touch  # noqa: F401
