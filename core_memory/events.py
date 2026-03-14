"""Legacy compatibility shim: moved to core_memory.persistence.events."""

from __future__ import annotations

import os
import warnings

if str(os.getenv("CORE_MEMORY_BLOCK_LEGACY_PERSISTENCE_SHIMS", "0")).strip().lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("legacy_persistence_shim_blocked: import core_memory.persistence.events")

warnings.warn(
    "core_memory.events is deprecated; use core_memory.persistence.events",
    DeprecationWarning,
    stacklevel=2,
)

from core_memory.persistence.events import *  # noqa: F401,F403
