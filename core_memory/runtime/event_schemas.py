"""Compatibility import path for Core Memory event schema constants.

Canonical definitions live in ``core_memory.schema.event_schemas``. This module
is retained for existing callers and persisted-data compatibility tests.
"""
from __future__ import annotations

from core_memory.schema.event_schemas import (
    CRAWLER_UPDATE,
    CRAWLER_UPDATE_LEGACY,
    FLUSH_CHECKPOINT,
    FLUSH_CHECKPOINT_LEGACY,
    FLUSH_REPORT,
    FLUSH_REPORT_LEGACY,
    HEALTH_REPORT,
    HEALTH_REPORT_LEGACY,
    MEMORY_EVENT,
    MEMORY_EVENT_LEGACY,
    TURN_ENVELOPE,
    TURN_ENVELOPE_LEGACY,
    is_crawler_update,
    is_flush_checkpoint,
    is_flush_report,
)

__all__ = [
    "CRAWLER_UPDATE",
    "CRAWLER_UPDATE_LEGACY",
    "FLUSH_CHECKPOINT",
    "FLUSH_CHECKPOINT_LEGACY",
    "FLUSH_REPORT",
    "FLUSH_REPORT_LEGACY",
    "HEALTH_REPORT",
    "HEALTH_REPORT_LEGACY",
    "MEMORY_EVENT",
    "MEMORY_EVENT_LEGACY",
    "TURN_ENVELOPE",
    "TURN_ENVELOPE_LEGACY",
    "is_crawler_update",
    "is_flush_checkpoint",
    "is_flush_report",
]
