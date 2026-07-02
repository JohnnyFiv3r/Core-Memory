from __future__ import annotations

import json
from pathlib import Path

import pytest

from core_memory.runtime.state import TurnEnvelope, emit_memory_event
from core_memory.persistence.turn_archive import (
    append_turn_record,
    get_turn_record,
    rebuild_all_indexes,
    rebuild_session_index,
)
try:
    from core_memory.identifiers import validate_archive_id
except ImportError:
    validate_archive_id = None


def test_emit_memory_event_writes_turn_archive_and_index(tmp_path: Path):
    env = TurnEnvelope(
        session_id="s-1",
        turn_id="t-1",
        transaction_id="tx-1",
        trace_id="tr-1",
        turns=[{"speaker": "user", "role": "user", "content": "why did we switch"}, {"speaker": "assistant", "role": "assistant", "content": "because latency dropped"}],
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


def test_append_turn_record_rejects_path_traversal_session_id(tmp_path: Path):
    outside = tmp_path / "outside"
    root = tmp_path / "memory"

    with pytest.raises(ValueError, match="invalid_session_id"):
        append_turn_record(
            root=root,
            session_id="x/../../outside/payload",
            turn_id="t-1",
            transaction_id="tx-1",
            trace_id="tr-1",
            origin="USER_TURN",
            ts="2026-01-01T00:00:00Z",
            user_query="",
            assistant_final=None,
            turns=[{"speaker": "user", "role": "user", "content": "attacker bytes"}],
            speakers=["user"],
            assistant_final_ref=None,
            assistant_final_hash="h",
            tools_trace=[],
            mesh_trace=[],
            metadata={},
        )

    assert not outside.exists()
    assert not (tmp_path / "outside" / "payload.jsonl").exists()


def test_append_turn_record_rejects_path_traversal_turn_id(tmp_path: Path):
    with pytest.raises(ValueError, match="invalid_turn_id"):
        append_turn_record(
            root=tmp_path / "memory",
            session_id="s-1",
            turn_id="x/../../outside/payload",
            transaction_id="tx-1",
            trace_id="tr-1",
            origin="USER_TURN",
            ts="2026-01-01T00:00:00Z",
            user_query="",
            assistant_final=None,
            turns=[{"speaker": "user", "role": "user", "content": "attacker bytes"}],
            speakers=["user"],
            assistant_final_ref=None,
            assistant_final_hash="h",
            tools_trace=[],
            mesh_trace=[],
            metadata={},
        )

    assert not (tmp_path / "outside").exists()


def test_validate_archive_id_rejects_padded_ids():
    with pytest.raises(ValueError, match="invalid_session_id"):
        validate_archive_id(" s-1", field="session_id")
    with pytest.raises(ValueError, match="invalid_session_id"):
        validate_archive_id("s-1 ", field="session_id")


def test_rebuild_session_index_rejects_invalid_session_id_without_raising(tmp_path: Path):
    result = rebuild_session_index(root=tmp_path, session_id="legacy session")
    assert result["ok"] is False
    assert result["error"] == "invalid_session_id"


def test_rebuild_all_indexes_skips_invalid_legacy_session_files(tmp_path: Path):
    turns_dir = tmp_path / ".turns"
    turns_dir.mkdir(parents=True)
    good = turns_dir / "session-good.jsonl"
    bad = turns_dir / "session-legacy session.jsonl"
    good.write_text(json.dumps({"turn_id": "t-1"}) + "\n", encoding="utf-8")
    bad.write_text(json.dumps({"turn_id": "t-legacy"}) + "\n", encoding="utf-8")

    result = rebuild_all_indexes(root=tmp_path)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["skipped_count"] == 1
    assert result["sessions"][0]["session_id"] == "good"
    assert result["skipped"][0]["session_id"] == "legacy session"
    assert (turns_dir / "session-good.idx.json").exists()
    assert not (turns_dir / "session-legacy session.idx.json").exists()
