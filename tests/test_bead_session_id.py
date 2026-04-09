from __future__ import annotations

from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.state import TurnEnvelope, emit_memory_event


def _emit_turn(root: Path, session_id: str, turn_id: str):
    env = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=f"tx-{turn_id}",
        trace_id=f"tr-{turn_id}",
        user_query="u",
        assistant_final="a",
    )
    emit_memory_event(root, env)


def test_add_bead_infers_session_id_from_source_turn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CORE_MEMORY_BEAD_SESSION_ID_MODE", "infer")
    _emit_turn(tmp_path, "sess-x", "t-1")

    s = MemoryStore(root=str(tmp_path))
    bead_id = s.add_bead(
        type="context",
        title="x",
        summary=["y"],
        source_turn_ids=["t-1"],
        detail="z",
        session_id=None,
    )

    idx = s._read_json(s.beads_dir / "index.json")
    bead = idx["beads"][bead_id]
    assert bead["session_id"] == "sess-x"


def test_add_bead_strict_mode_requires_session_id(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CORE_MEMORY_BEAD_SESSION_ID_MODE", "strict")
    s = MemoryStore(root=str(tmp_path))

    try:
        s.add_bead(
            type="context",
            title="x",
            summary=["y"],
            source_turn_ids=[],
            detail="z",
            session_id=None,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "missing:session_id" in str(exc)

