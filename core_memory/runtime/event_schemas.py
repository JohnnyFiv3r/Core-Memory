"""Canonical event schema string constants for Core Memory events.

Phase 9b: schema strings no longer embed the openclaw namespace.
Legacy aliases are kept so consumers reading previously-persisted events
can accept both the old and new strings.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical event schema strings (emitted from this version forward)
# ---------------------------------------------------------------------------

FLUSH_REPORT       = "core-memory.flush_report.v1"
FLUSH_CHECKPOINT   = "core-memory.flush_checkpoint.v1"
CRAWLER_UPDATE     = "core-memory.crawler_update.v1"
TURN_ENVELOPE      = "core-memory.turn_envelope.v1"
MEMORY_EVENT       = "core-memory.event.v1"
HEALTH_REPORT      = "core-memory.canonical_health_report.v1"

# ---------------------------------------------------------------------------
# Legacy aliases — deprecated, accepted on READ path only
# ---------------------------------------------------------------------------

FLUSH_REPORT_LEGACY      = "openclaw.memory.flush_report.v1"
FLUSH_CHECKPOINT_LEGACY  = "openclaw.memory.flush_checkpoint.v1"
CRAWLER_UPDATE_LEGACY    = "openclaw.memory.crawler_update.v1"
TURN_ENVELOPE_LEGACY     = "openclaw.memory.turn_envelope.v1"
MEMORY_EVENT_LEGACY      = "openclaw.memory.event.v1"
HEALTH_REPORT_LEGACY     = "openclaw.memory.canonical_health_report.v1"


def is_flush_report(schema: str) -> bool:
    return schema in (FLUSH_REPORT, FLUSH_REPORT_LEGACY)


def is_flush_checkpoint(schema: str) -> bool:
    return schema in (FLUSH_CHECKPOINT, FLUSH_CHECKPOINT_LEGACY)


def is_crawler_update(schema: str) -> bool:
    return schema in (CRAWLER_UPDATE, CRAWLER_UPDATE_LEGACY)
