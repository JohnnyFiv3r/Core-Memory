from __future__ import annotations

from core_memory.integrations.openclaw_agent_end_bridge import process_agent_end_event
from core_memory.integrations.openclaw_compaction_bridge import process_compaction_event


def test_agent_end_bridge_skips_when_core_memory_disabled(monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_ENABLED", "0")
    out = process_agent_end_event(
        event={"assistant": "ok", "sessionId": "s1"},
        ctx={"sessionId": "s1"},
        root=".",
    )
    assert out["ok"] is True
    assert out["emitted"] is False
    assert out["reason"] == "core_memory_disabled"


def test_compaction_bridge_skips_when_core_memory_disabled(monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_ENABLED", "0")
    out = process_compaction_event(
        event={"sessionId": "s1", "runId": "r1"},
        ctx={"sessionId": "s1"},
        root=".",
    )
    assert out["ok"] is True
    assert out["flushed"] is False
    assert out["reason"] == "core_memory_disabled"

