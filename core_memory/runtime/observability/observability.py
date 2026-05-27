"""Structured observability for Core Memory operations.

Emits structured JSON log records for key operations:
- bead_added, bead_promoted, bead_compacted
- query_executed, search_executed, reason_executed
- flush_completed, rebuild_completed
- turn_processed

Also tracks in-memory counters for a /v1/metrics endpoint.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("core_memory.observability")

# In-memory counters (reset on process restart)
_counters: dict[str, int] = {
    "beads_added": 0,
    "beads_promoted": 0,
    "beads_compacted": 0,
    "queries_executed": 0,
    "searches_executed": 0,
    "reasons_executed": 0,
    "turns_processed": 0,
    "flushes_completed": 0,
    "errors": 0,
}

_timings: dict[str, list[float]] = {
    "add_bead_ms": [],
    "query_ms": [],
    "search_ms": [],
    "reason_ms": [],
    "flush_ms": [],
}

MAX_TIMING_SAMPLES = 100


def emit_event(event_type: str, **kwargs: Any) -> None:
    """Emit a structured observability event."""
    record = {"event": event_type, "ts": time.time(), **kwargs}
    logger.info(json.dumps(record, ensure_ascii=False, default=str))


def increment(counter: str, n: int = 1) -> None:
    """Increment a counter."""
    if counter in _counters:
        _counters[counter] += n


def record_timing(operation: str, duration_ms: float) -> None:
    """Record a timing sample."""
    key = f"{operation}_ms"
    if key in _timings:
        samples = _timings[key]
        samples.append(duration_ms)
        if len(samples) > MAX_TIMING_SAMPLES:
            _timings[key] = samples[-MAX_TIMING_SAMPLES:]


def get_metrics() -> dict[str, Any]:
    """Get current metrics snapshot."""
    metrics: dict[str, Any] = {"counters": dict(_counters)}
    timings_summary: dict[str, Any] = {}
    for key, samples in _timings.items():
        if samples:
            timings_summary[key] = {
                "count": len(samples),
                "avg": round(sum(samples) / len(samples), 2),
                "min": round(min(samples), 2),
                "max": round(max(samples), 2),
                "last": round(samples[-1], 2),
            }
    metrics["timings"] = timings_summary
    return metrics


class Timer:
    """Context manager for timing operations."""

    def __init__(self, operation: str):
        self.operation = operation
        self.start_time = 0.0
        self.duration_ms = 0.0

    def __enter__(self) -> "Timer":
        self.start_time = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        self.duration_ms = (time.monotonic() - self.start_time) * 1000
        record_timing(self.operation, self.duration_ms)
