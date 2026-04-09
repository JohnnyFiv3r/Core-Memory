from __future__ import annotations

from pathlib import Path

from core_memory.integrations.api import (
    get_adjacent_turns,
    get_turn_tools,
    hydrate_bead_sources,
)
from core_memory.runtime.state import TurnEnvelope, emit_memory_event
from core_memory.persistence.store import MemoryStore


def _emit(root: Path, session_id: str, turn_id: str, text: str, tools=None):
    env = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=f"tx-{turn_id}",
        trace_id=f"tr-{turn_id}",
        user_query=f"q-{turn_id}",
        assistant_final=text,
        tools_trace=tools or [],
    )
    emit_memory_event(root, env)


def test_get_turn_tools_and_adjacent(tmp_path: Path):
    _emit(tmp_path, "s1", "t1", "one")
    _emit(tmp_path, "s1", "t2", "two", tools=[{"tool_call_id": "a", "category": "search", "result_hash": "h"}])
    _emit(tmp_path, "s1", "t3", "three")

    tools = get_turn_tools(root=str(tmp_path), turn_id="t2", session_id="s1")
    assert tools is not None
    assert tools["turn_id"] == "t2"
    assert tools["tools_trace"][0]["category"] == "search"

    adj = get_adjacent_turns(root=str(tmp_path), turn_id="t2", session_id="s1", before=1, after=1)
    assert adj is not None
    assert adj["pivot"]["turn_id"] == "t2"
    assert adj["before"][0]["turn_id"] == "t1"
    assert adj["after"][0]["turn_id"] == "t3"


def test_hydrate_bead_sources_by_bead_id(tmp_path: Path):
    _emit(tmp_path, "s1", "t10", "decision body", tools=[{"tool_call_id": "x", "category": "calc", "result_hash": "z"}])

    store = MemoryStore(root=str(tmp_path))
    bead_id = store.add_bead(
        type="decision",
        title="picked approach",
        summary=["why"],
        detail="because",
        session_id="s1",
        source_turn_ids=["t10"],
    )

    out = hydrate_bead_sources(
        root=str(tmp_path),
        bead_ids=[bead_id],
        include_tools=True,
        before=0,
        after=0,
    )
    assert out["beads"][0]["id"] == bead_id
    assert out["hydrated"][0]["turn"]["turn_id"] == "t10"
    assert out["hydrated"][0]["tools"]["tools_trace"][0]["category"] == "calc"

