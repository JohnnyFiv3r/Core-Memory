from __future__ import annotations

from typing import Any

from core_memory.integrations.crewai import memory as crewai_memory


def test_short_term_save_uses_canonical_turn_kwargs(monkeypatch, tmp_path):
    calls: list[dict[str, Any]] = []

    def fake_process_turn_finalized(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "bead_id": "b1"}

    monkeypatch.setattr(crewai_memory, "process_turn_finalized", fake_process_turn_finalized)

    mem = crewai_memory.CoreMemoryShortTerm(root=str(tmp_path), session_id="s-crewai")
    mem.save("remember this", metadata={"type": "decision", "tags": ["crewai"], "source_turn_ids": ["t0"]})

    assert len(calls) == 1
    call = calls[0]
    assert call["root"] == str(tmp_path)
    assert call["session_id"] == "s-crewai"
    assert call["metadata"] == {"type": "decision", "tags": ["crewai"], "source_turn_ids": ["t0"]}
    assert "user_query" not in call
    assert "assistant_response" not in call
    assert call["turns"] == [{"speaker": "crewai", "role": "assistant", "content": "remember this"}]


def test_long_term_and_entity_save_use_canonical_turn_kwargs(monkeypatch, tmp_path):
    calls: list[dict[str, Any]] = []

    def fake_process_turn_finalized(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(crewai_memory, "process_turn_finalized", fake_process_turn_finalized)

    crewai_memory.CoreMemoryLongTerm(root=str(tmp_path)).save("durable lesson")
    crewai_memory.CoreMemoryEntity(root=str(tmp_path)).save("entity note", metadata={"tags": ["entity"]})

    assert [call["session_id"] for call in calls] == ["crewai-long-term", "crewai-entity"]
    assert all(call["root"] == str(tmp_path) for call in calls)
    assert all("user_query" not in call and "assistant_response" not in call for call in calls)
    assert calls[0]["turns"] == [{"speaker": "crewai", "role": "assistant", "content": "durable lesson"}]
    assert calls[0]["metadata"]["type"] == "lesson"
    assert calls[1]["turns"] == [{"speaker": "crewai", "role": "assistant", "content": "entity note"}]
    assert calls[1]["metadata"]["type"] == "context"
