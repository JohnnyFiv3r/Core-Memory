"""MCP `recall` tool wrapper."""

from __future__ import annotations

from typing import Any

from core_memory.integrations.recall_payload import run_recall_payload


def recall_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_recall_payload(payload, surface="recall")
