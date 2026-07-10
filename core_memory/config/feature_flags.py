"""Runtime feature flags for Core Memory.

All flags read from environment variables. This module has no dependencies on
any other Core Memory module — safe to import from any layer.

Moved here from integrations/openclaw_flags.py (Phase 9a). The openclaw-specific
flag (supersede_openclaw_summary_enabled) remains in integrations/openclaw_flags.py.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    v = str(raw).strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def core_memory_enabled() -> bool:
    return _env_bool("CORE_MEMORY_ENABLED", True)


def transcript_archive_enabled() -> bool:
    return _env_bool("CORE_MEMORY_TRANSCRIPT_ARCHIVE", True)


def transcript_hydration_enabled() -> bool:
    return _env_bool("CORE_MEMORY_TRANSCRIPT_HYDRATION", True)


def soul_promotion_enabled() -> bool:
    return _env_bool("CORE_MEMORY_SOUL_PROMOTION", False)


def default_hydrate_tools_enabled() -> bool:
    return _env_bool("CORE_MEMORY_DEFAULT_HYDRATE_TOOLS", False)


def default_adjacent_turns() -> int:
    raw = os.environ.get("CORE_MEMORY_DEFAULT_ADJACENT_TURNS")
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def agent_authored_required_enabled() -> bool:
    """When enabled, semantic turn memory must come from agent-authored payloads."""
    return _env_bool("CORE_MEMORY_AGENT_AUTHORED_REQUIRED", False)


def agent_authored_fail_open_enabled() -> bool:
    return _env_bool("CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN", False)


def agent_crawler_invoke_enabled() -> bool:
    return _env_bool("CORE_MEMORY_AGENT_CRAWLER_INVOKE", False)


def agent_crawler_max_attempts() -> int:
    raw = os.environ.get("CORE_MEMORY_AGENT_CRAWLER_MAX_ATTEMPTS")
    try:
        return max(1, int(raw or 2))
    except ValueError:
        return 2


def bead_judge_fallback_enabled() -> bool:
    """Allow Core Memory to re-author semantic bead fields as an explicit fallback.

    Default is off: adapters/agents own semantic memory authorship. This fallback
    exists for legacy demos and repair tooling, not the normal write path.
    """
    return _env_bool("CORE_MEMORY_BEAD_JUDGE_FALLBACK", False)


def agent_authored_repair_enabled() -> bool:
    """Allow an explicit full-contract delegated repair attempt.

    Repair is off by default and never uses the narrow bead-field judge. The
    normal hard-authorship path remains pending until an operator or runtime
    policy explicitly enables this attributed repair flow.
    """

    return _env_bool("CORE_MEMORY_AGENT_AUTHORED_REPAIR", False)


def agent_min_semantic_associations_after_first() -> int:
    raw = os.environ.get("CORE_MEMORY_AGENT_MIN_SEMANTIC_ASSOC_AFTER_FIRST")
    try:
        return max(0, int(raw or 1))
    except ValueError:
        return 1


def preview_association_promotion_enabled() -> bool:
    return _env_bool("CORE_MEMORY_PREVIEW_ASSOC_PROMOTION", False)


def preview_association_allow_shared_tag() -> bool:
    return _env_bool("CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG", False)


def agent_authored_mode() -> str:
    """Gate severity for agent-authored turn memory (F-W2).

    Values: hard | warn | off. Legacy aliases: enforce→hard, observe→off.
    """
    raw = str(os.environ.get("CORE_MEMORY_AGENT_AUTHORED_MODE") or "").strip().lower()
    if raw in {"hard", "warn", "off"}:
        return raw
    if raw == "enforce":
        return "hard"
    if raw == "observe":
        return "off"
    req = agent_authored_required_enabled()
    if req:
        # REQUIRED=1 always means hard gate; FAIL_OPEN is a legacy hint that has
        # no effect when REQUIRED is set (agent-authored updates are mandatory).
        return "hard"
    return "hard"


def resolved_agent_authored_gate() -> dict[str, object]:
    mode = agent_authored_mode()
    if mode in {"hard", "enforce"}:
        return {"mode": mode, "required": True, "fail_open": False}
    if mode == "warn":
        # warn: gate logic runs but does not block or mark beads; just emits metrics
        return {"mode": mode, "required": False, "fail_open": True}
    return {"mode": "off", "required": False, "fail_open": True}


def claim_layer_enabled() -> bool:
    return _env_bool("CORE_MEMORY_CLAIM_LAYER", False)


def claim_extraction_mode() -> str:
    """Returns: 'off', 'heuristic', or 'llm'"""
    val = os.environ.get("CORE_MEMORY_CLAIM_EXTRACTION_MODE", "off").strip().lower()
    if val not in ("off", "heuristic", "llm"):
        return "off"
    return val


def claim_resolution_enabled() -> bool:
    return _env_bool("CORE_MEMORY_CLAIM_RESOLUTION", False)


def claim_retrieval_boost_enabled() -> bool:
    return _env_bool("CORE_MEMORY_CLAIM_RETRIEVAL_BOOST", False)


def external_pipehouse_url() -> str:
    """Base URL for optional PipeHouse evidence fan-out in recall()."""
    return (os.environ.get("CORE_MEMORY_PIPEHOUSE_URL") or "").strip()


def external_store_weights() -> str:
    """Comma-separated weights for core_memory,pipehouse fan-out.

    Legacy core_memory,ragie,pipehouse values are accepted by the fan-out parser.
    """
    return (os.environ.get("CORE_MEMORY_STORE_WEIGHTS") or "").strip()


def runtime_flags_snapshot() -> dict[str, object]:
    """Snapshot of all generic Core Memory flags.

    The openclaw-specific supersede_openclaw_summary_enabled flag is included
    by the backward-compat shim in integrations/openclaw_flags.py.
    """
    return {
        "core_memory_enabled": core_memory_enabled(),
        "transcript_archive_enabled": transcript_archive_enabled(),
        "transcript_hydration_enabled": transcript_hydration_enabled(),
        "soul_promotion_enabled": soul_promotion_enabled(),
        "default_hydrate_tools_enabled": default_hydrate_tools_enabled(),
        "default_adjacent_turns": default_adjacent_turns(),
        "agent_authored_required_enabled": agent_authored_required_enabled(),
        "agent_authored_fail_open_enabled": agent_authored_fail_open_enabled(),
        "agent_authored_mode": agent_authored_mode(),
        "agent_authored_gate_resolved": resolved_agent_authored_gate(),
        "agent_crawler_invoke_enabled": agent_crawler_invoke_enabled(),
        "agent_crawler_max_attempts": agent_crawler_max_attempts(),
        "bead_judge_fallback_enabled": bead_judge_fallback_enabled(),
        "agent_authored_repair_enabled": agent_authored_repair_enabled(),
        "agent_min_semantic_associations_after_first": agent_min_semantic_associations_after_first(),
        "preview_association_promotion_enabled": preview_association_promotion_enabled(),
        "preview_association_allow_shared_tag": preview_association_allow_shared_tag(),
    }
