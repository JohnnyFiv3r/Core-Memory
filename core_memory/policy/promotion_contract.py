"""Public promotion-state contract import surface.

The canonical stored-bead promotion-state helpers live in
``core_memory.schema.promotion_contract`` so persistence can depend on them
without crossing an upward layer boundary.
"""
from __future__ import annotations

from core_memory.schema.promotion_contract import *  # noqa: F401,F403
