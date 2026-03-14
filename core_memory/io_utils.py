"""Legacy compatibility shim: moved to core_memory.persistence.io_utils."""

from __future__ import annotations

import os
import warnings

if str(os.getenv("CORE_MEMORY_BLOCK_LEGACY_PERSISTENCE_SHIMS", "0")).strip().lower() in {"1", "true", "yes", "on"}:
    raise RuntimeError("legacy_persistence_shim_blocked: import core_memory.persistence.io_utils")

warnings.warn(
    "core_memory.io_utils is deprecated; use core_memory.persistence.io_utils",
    DeprecationWarning,
    stacklevel=2,
)

from core_memory.persistence.io_utils import *  # noqa: F401,F403
