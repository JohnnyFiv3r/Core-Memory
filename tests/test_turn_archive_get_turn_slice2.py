from __future__ import annotations

from pathlib import Path

from core_memory.integrations.api import get_turn
from core_memory.runtime.state import TurnEnvelope, emit_memory_event


def _emit(root: Path, session_id: str, turn_id: str, text: str) -> None:
    env = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=f"tx-{turn_id}",
        trace_id=f"tr-{turn_id}",
        user_query=f"q-{turn_id}",
        assistant_final=text,
    )
    emit_memory_event(root, env)


def test_get_turn_by_id_cross_session(tmp_path: Path):
    _emit(tmp_path, "s1", "t-100", "alpha")
    _emit(tmp_path, "s2", "t-200", "beta")

    row = get_turn(root=str(tmp_path), turn_id="t-200")
    assert row is not None
    assert row["session_id"] == "s2"
    assert row["assistant_final"] == "beta"


def test_get_turn_with_session_hint(tmp_path: Path):
    _emit(tmp_path, "s1", "t-1", "one")
    _emit(tmp_path, "s2", "t-1", "two")

    row = get_turn(root=str(tmp_path), turn_id="t-1", session_id="s1")
    assert row is not None
    assert row["session_id"] == "s1"
    assert row["assistant_final"] == "one"


def test_get_turn_missing_returns_none(tmp_path: Path):
    assert get_turn(root=str(tmp_path), turn_id="does-not-exist") is None

