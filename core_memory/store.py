"""Legacy compatibility shim.

Canonical store lives at core_memory.persistence.store.
"""

from __future__ import annotations

import os
import warnings

if str(os.getenv("CORE_MEMORY_BLOCK_LEGACY_STORE_SHIM", "0")).strip().lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("legacy_store_shim_blocked: import core_memory.persistence.store instead")

warnings.warn(
    "core_memory.store is deprecated; use core_memory.persistence.store",
    DeprecationWarning,
    stacklevel=2,
)

from core_memory.persistence.store import *  # noqa: F401,F403
