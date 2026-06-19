"""OpenClaw integration for Core Memory.

Public entry points:
- openclaw.runtime      — coordinator finalize hooks
- openclaw.agent_end_bridge — agent-end write bridge
- openclaw.hosted_capture_bridge — hosted HTTP turn-finalized clone bridge
- openclaw.read_bridge  — read dispatch bridge
- openclaw.compaction_bridge / compaction_queue — flush hooks
- openclaw.onboard      — plugin installer
- openclaw.flags        — OpenClaw-specific feature flag (supersede_openclaw_summary_enabled)

Generic Core Memory feature flags live in core_memory.config.feature_flags.
"""
