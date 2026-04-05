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
    }
