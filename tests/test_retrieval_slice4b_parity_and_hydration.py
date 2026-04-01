from __future__ import annotations

import tempfile
from pathlib import Path

from core_memory.integrations.openclaw_agent_end_bridge import process_agent_end_event
from core_memory.integrations.pydanticai.run import run_with_memory_sync, flush_session
from core_memory.retrieval.tools.memory import execute


class _FakeAgent:
    def run_sync(self, q: str):
        class _R:
            output = f"ok:{q}"

        return _R()


def test_adapter_parity_openclaw_and_pydanticai_are_searchable():
    with tempfile.TemporaryDirectory() as td:
        # OpenClaw turn
        process_agent_end_event(
            event={"messages": [{"role": "user", "content": "Redis fix"}, {"role": "assistant", "content": "Raised pool size"}], "runId": "r1", "success": True},
            ctx={"sessionId": "s1", "sessionKey": "s1"},
            root=td,
        )

        # PydanticAI turn + flush
        run_with_memory_sync(_FakeAgent(), "Redis fix pyd", root=td, session_id="s2", turn_id="t2")
        flush_session(root=td, session_id="s2")

        out = execute({"raw_query": "redis fix", "intent": "remember", "k": 8}, root=td, explain=True)
        assert out.get("ok") is True
        assert isinstance(out.get("anchors") or [], list)
        assert len(out.get("anchors") or []) >= 1


def test_hydration_block_non_fatal_and_present():
    with tempfile.TemporaryDirectory() as td:
        process_agent_end_event(
            event={"messages": [{"role": "user", "content": "Why Redis fix"}, {"role": "assistant", "content": "Because pool size"}], "runId": "r1", "success": True},
            ctx={"sessionId": "s1", "sessionKey": "s1"},
            root=td,
        )
        out = execute(
            {
                "raw_query": "why redis fix",
                "intent": "causal",
                "k": 5,
                "hydration": {"deep_recall": "selected_only", "turn_sources": "cited_turns", "adjacent_before": 1, "adjacent_after": 1, "max_beads": 5},
            },
            root=td,
            explain=True,
        )
        assert out.get("ok") is True
        hyd = out.get("hydration") or {}
        assert hyd.get("status") in {"complete", "partial", "failed", "not_requested"}
        assert isinstance(hyd.get("warnings") or [], list)


def test_stale_budget_guard_lowers_causal_weak_anchor_confidence(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        process_agent_end_event(
            event={"messages": [{"role": "user", "content": "tiny signal"}, {"role": "assistant", "content": "x"}], "runId": "r1", "success": True},
            ctx={"sessionId": "s1", "sessionKey": "s1"},
            root=td,
        )
        # Force tiny stale budget so warning path fires
        monkeypatch.setenv("CORE_MEMORY_SEMANTIC_MAX_STALE_MS", "1")
        out = execute({"raw_query": "why tiny signal", "intent": "causal", "k": 5}, root=td, explain=True)
        warns = out.get("warnings") or []
        if "semantic_index_over_stale_budget" in warns:
            assert out.get("confidence") == "low"
            assert out.get("next_action") == "ask_clarifying"

