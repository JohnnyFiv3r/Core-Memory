from __future__ import annotations

import json
from pathlib import Path

from core_memory.integrations.migration import rebuild_turn_indexes, backfill_bead_session_ids
from core_memory.runtime.state import TurnEnvelope, emit_memory_event
from core_memory.persistence.store import MemoryStore


def _emit(root: Path, sid: str, tid: str):
    env = TurnEnvelope(
        session_id=sid,
        turn_id=tid,
        transaction_id=f"tx-{tid}",
        trace_id=f"tr-{tid}",
        user_query="u",
        assistant_final="a",
    )
    emit_memory_event(root, env)


def test_rebuild_turn_indexes(tmp_path: Path):
    _emit(tmp_path, "s1", "t1")
    _emit(tmp_path, "s1", "t2")

    idx_file = tmp_path / ".turns" / "session-s1.idx.json"
    idx_file.write_text("{}", encoding="utf-8")

    out = rebuild_turn_indexes(root=str(tmp_path))
    assert out["ok"] is True
    idx = json.loads(idx_file.read_text(encoding="utf-8"))
    assert "t1" in idx and "t2" in idx


def test_backfill_bead_session_ids(tmp_path: Path):
    _emit(tmp_path, "sess-z", "t100")
    s = MemoryStore(root=str(tmp_path))
    bead_id = s.add_bead(
        type="context",
        title="x",
        summary=["y"],
        source_turn_ids=["t100"],
        detail="z",
        session_id="sess-z",
    )

    idx_path = s.beads_dir / "index.json"
    idx = s._read_json(idx_path)
    idx["beads"][bead_id]["session_id"] = ""
    s._write_json(idx_path, idx)

    out = backfill_bead_session_ids(root=str(tmp_path))
    assert out["ok"] is True
    assert out["updated"] >= 1

    idx2 = s._read_json(idx_path)
    assert idx2["beads"][bead_id]["session_id"] == "sess-z"

