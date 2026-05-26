"""Backward-compat shim. Canonical location: core_memory.config.feature_flags.

All generic Core Memory flags have moved to core_memory/config/feature_flags.py (Phase 9a).
This shim re-exports them so existing callers continue to work.

The one OpenClaw-specific flag (supersede_openclaw_summary_enabled) stays here and will
move to integrations/openclaw/flags.py in Phase 9c.
"""
from __future__ import annotations

import os

# Re-export all generic flags from the canonical location.
from core_memory.config.feature_flags import (  # noqa: F401
    _env_bool,
    agent_authored_fail_open_enabled,
    agent_authored_mode,
    agent_authored_required_enabled,
    agent_crawler_invoke_enabled,
    agent_crawler_max_attempts,
    agent_min_semantic_associations_after_first,
    claim_extraction_mode,
    claim_layer_enabled,
    claim_resolution_enabled,
    claim_retrieval_boost_enabled,
    core_memory_enabled,
    default_adjacent_turns,
    default_hydrate_tools_enabled,
    preview_association_allow_shared_tag,
    preview_association_promotion_enabled,
    resolved_agent_authored_gate,
    soul_promotion_enabled,
    transcript_archive_enabled,
    transcript_hydration_enabled,
)
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
