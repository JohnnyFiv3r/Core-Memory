from __future__ import annotations

from pathlib import Path

from core_memory.integrations.api import get_turn, hydrate_bead_sources
from core_memory.integrations.openclaw_flags import runtime_flags_snapshot
from core_memory.integrations.openclaw_onboard import run_openclaw_onboard
from core_memory.runtime.state import TurnEnvelope, emit_memory_event


def test_transcript_archive_flag_disables_turn_archive(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CORE_MEMORY_TRANSCRIPT_ARCHIVE", "0")
    env = TurnEnvelope(
        session_id="s1",
        turn_id="t1",
        transaction_id="tx1",
        trace_id="tr1",
        user_query="q",
        assistant_final="a",
    )
    emit_memory_event(tmp_path, env)
    turns_file = tmp_path / ".turns" / "session-s1.jsonl"
    assert not turns_file.exists()


def test_transcript_hydration_flag_disables_get_turn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CORE_MEMORY_TRANSCRIPT_HYDRATION", "0")
    out = get_turn(root=str(tmp_path), turn_id="t1")
    assert out is None

    h = hydrate_bead_sources(root=str(tmp_path), bead_ids=["x"])
    assert h.get("disabled") is True


def test_onboard_uses_supersede_flag_by_default(monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY", "1")
    out = run_openclaw_onboard(dry_run=True)
    assert out["replace_memory_core"] is True
    cmds = [" ".join(step.get("cmd") or []) for step in out.get("steps") or []]
    assert any("plugins disable memory-core" in c for c in cmds)


def test_flags_snapshot_shape():
    snap = runtime_flags_snapshot()
    assert "core_memory_enabled" in snap
    assert "supersede_openclaw_summary_enabled" in snap
    assert "agent_min_semantic_associations_after_first" in snap
    assert "preview_association_promotion_enabled" in snap
    assert "preview_association_allow_shared_tag" in snap
