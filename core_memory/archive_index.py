"""Legacy compatibility shim: moved to core_memory.persistence.archive_index."""

from __future__ import annotations

import os
import warnings

if str(os.getenv("CORE_MEMORY_BLOCK_LEGACY_PERSISTENCE_SHIMS", "0")).strip().lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("legacy_persistence_shim_blocked: import core_memory.persistence.archive_index")

warnings.warn(
    "core_memory.archive_index is deprecated; use core_memory.persistence.archive_index",
    DeprecationWarning,
    stacklevel=2,
)

from core_memory.persistence.archive_index import *  # noqa: F401,F403
