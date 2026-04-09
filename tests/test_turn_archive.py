from __future__ import annotations

import json
from pathlib import Path

from core_memory.runtime.state import TurnEnvelope, emit_memory_event
from core_memory.runtime.turn_archive import get_turn_record


def test_emit_memory_event_writes_turn_archive_and_index(tmp_path: Path):
    env = TurnEnvelope(
        session_id="s-1",
        turn_id="t-1",
        transaction_id="tx-1",
        trace_id="tr-1",
        user_query="why did we switch",
        assistant_final="because latency dropped",
        tools_trace=[{"tool_call_id": "a", "category": "search", "result_hash": "h1"}],
        mesh_trace=[{"capability": "retrieval", "input_hash": "i1", "output_hash": "o1"}],
        metadata={"k": "v"},
    )

    event = emit_memory_event(tmp_path, env)
    assert event.turn_id == "t-1"

    turns_file = tmp_path / ".turns" / "session-s-1.jsonl"
    idx_file = tmp_path / ".turns" / "session-s-1.idx.json"
    assert turns_file.exists()
    assert idx_file.exists()

    lines = [ln for ln in turns_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["schema"] == "core_memory.turn_record.v1"
    assert row["session_id"] == "s-1"
    assert row["turn_id"] == "t-1"
    assert row["assistant_final_hash"]

    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    assert "t-1" in idx
    assert int(idx["t-1"]["offset"]) >= 0
    assert int(idx["t-1"]["length"]) > 0

    hydrated = get_turn_record(root=tmp_path, session_id="s-1", turn_id="t-1")
    assert hydrated is not None
    assert hydrated["turn_id"] == "t-1"
    assert hydrated["tools_trace"][0]["category"] == "search"

