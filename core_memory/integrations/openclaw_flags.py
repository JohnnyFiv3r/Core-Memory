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


def supersede_openclaw_summary_enabled() -> bool:
    return _env_bool("CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY", False)


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
    """When enabled, semantic turn memory must come from agent-authored payloads.

    Slice-0 scaffold only: runtime enforcement lands in follow-up slices.
    """
    return _env_bool("CORE_MEMORY_AGENT_AUTHORED_REQUIRED", False)


def agent_authored_fail_open_enabled() -> bool:
    """Allow deterministic fallback when agent-authored payload is missing/invalid.

    Intended default for strict mode is False.
    """
    return _env_bool("CORE_MEMORY_AGENT_AUTHORED_FAIL_OPEN", False)


def agent_crawler_invoke_enabled() -> bool:
    """Enable turn-time crawler agent invocation hook."""
    return _env_bool("CORE_MEMORY_AGENT_CRAWLER_INVOKE", False)


def agent_crawler_max_attempts() -> int:
    """Max attempts for crawler agent invocation per turn (bounded retries)."""
    raw = os.environ.get("CORE_MEMORY_AGENT_CRAWLER_MAX_ATTEMPTS")
    try:
        return max(1, int(raw or 2))
    except ValueError:
        return 2


def agent_min_semantic_associations_after_first() -> int:
    """Minimum non-temporal semantic associations required after first session turn.

    Enforced in strict agent-authored mode.
    """
    raw = os.environ.get("CORE_MEMORY_AGENT_MIN_SEMANTIC_ASSOC_AFTER_FIRST")
    try:
        return max(0, int(raw or 1))
    except ValueError:
        return 1


def preview_association_promotion_enabled() -> bool:
    """Enable deterministic promotion of store association_preview candidates.

    Default off for quality-first agent-authored association policy.
    """
    return _env_bool("CORE_MEMORY_PREVIEW_ASSOC_PROMOTION", False)


def preview_association_allow_shared_tag() -> bool:
    """Allow shared_tag relation during preview promotion when enabled."""
    return _env_bool("CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG", False)


def agent_authored_mode() -> str:
    """Gate severity for agent-authored turn memory (F-W2).

    Values (canonical):
    - hard: validate strictly, block turn on failure (no fallback)
    - warn: validate strictly, persist with structural_coverage_missing flag (default)
    - off: bypass gate entirely

    Legacy aliases accepted: enforce→hard, observe→off.
    """
    raw = str(os.environ.get("CORE_MEMORY_AGENT_AUTHORED_MODE") or "").strip().lower()
    # Canonical values
    if raw in {"hard", "warn", "off"}:
        return raw
    # Legacy aliases
    if raw == "enforce":
        return "hard"
    if raw == "observe":
        return "off"

    # Legacy flag derivation
    req = agent_authored_required_enabled()
    fail_open = agent_authored_fail_open_enabled()
    if req and not fail_open:
        return "hard"
    if req and fail_open:
        return "warn"
    # F-W2: default to warn in OSS (was observe/off)
    return "warn"


def resolved_agent_authored_gate() -> dict[str, object]:
    mode = agent_authored_mode()
    if mode == "hard":
        return {"mode": mode, "required": True, "fail_open": False}
    if mode == "warn":
        return {"mode": mode, "required": True, "fail_open": True}
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


def runtime_flags_snapshot() -> dict[str, object]:
    return {
        "core_memory_enabled": core_memory_enabled(),
        "transcript_archive_enabled": transcript_archive_enabled(),
        "transcript_hydration_enabled": transcript_hydration_enabled(),
        "supersede_openclaw_summary_enabled": supersede_openclaw_summary_enabled(),
        "soul_promotion_enabled": soul_promotion_enabled(),
        "default_hydrate_tools_enabled": default_hydrate_tools_enabled(),
        "default_adjacent_turns": default_adjacent_turns(),
        "agent_authored_required_enabled": agent_authored_required_enabled(),
        "agent_authored_fail_open_enabled": agent_authored_fail_open_enabled(),
        "agent_authored_mode": agent_authored_mode(),
        "agent_authored_gate_resolved": resolved_agent_authored_gate(),
        "agent_crawler_invoke_enabled": agent_crawler_invoke_enabled(),
        "agent_crawler_max_attempts": agent_crawler_max_attempts(),
        "agent_min_semantic_associations_after_first": agent_min_semantic_associations_after_first(),
        "preview_association_promotion_enabled": preview_association_promotion_enabled(),
        "preview_association_allow_shared_tag": preview_association_allow_shared_tag(),
    }
