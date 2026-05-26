"""OpenClaw-specific feature flags.

Generic Core Memory flags live in core_memory.config.feature_flags (moved there in Phase 9a).
This module re-exports all of those and adds the one OpenClaw-specific flag.
"""
from __future__ import annotations

import os

# Re-export all generic Core Memory flags so callers can import from here directly.
from core_memory.config.feature_flags import *  # noqa: F401, F403
from core_memory.config.feature_flags import runtime_flags_snapshot as _base_runtime_flags_snapshot


def supersede_openclaw_summary_enabled() -> bool:
    raw = os.environ.get("CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY")
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def runtime_flags_snapshot() -> dict[str, object]:
    """Full snapshot including the openclaw-specific supersede flag."""
    snap = dict(_base_runtime_flags_snapshot())
    snap["supersede_openclaw_summary_enabled"] = supersede_openclaw_summary_enabled()
    return snap
