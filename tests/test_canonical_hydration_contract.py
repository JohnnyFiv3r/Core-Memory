from __future__ import annotations

from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import execute_request, trace_request
from core_memory.runtime.state import TurnEnvelope, emit_memory_event


def _emit(root: Path, session_id: str, turn_id: str, text: str) -> None:
    env = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=f"tx-{turn_id}",
        trace_id=f"tr-{turn_id}",
        user_query=f"q-{turn_id}",
        assistant_final=text,
        tools_trace=[{"tool_call_id": f"tool-{turn_id}", "category": "search", "result_hash": "h"}],
    )
    emit_memory_event(root, env)


def _seed(root: Path) -> tuple[str, str]:
    _emit(root, "s1", "t1", "one")
    _emit(root, "s1", "t2", "two")
    _emit(root, "s1", "t3", "three")

    store = MemoryStore(root=str(root))
    b1 = store.add_bead(
        type="decision",
        title="alpha",
        summary=["alpha"],
        session_id="s1",
        source_turn_ids=["t2"],
    )
    b2 = store.add_bead(
        type="decision",
        title="beta",
        summary=["beta"],
        session_id="s1",
        source_turn_ids=["t3"],
    )
    return b1, b2


def test_cited_turns_mode_disables_adjacency(tmp_path: Path):
    b1, _ = _seed(tmp_path)

    out = trace_request(
        root=tmp_path,
        anchor_ids=[b1],
        hydration={
            "turn_sources": "cited_turns",
            "adjacent_before": 2,
            "adjacent_after": 2,
            "max_beads": 5,
        },
    )

    hyd = out.get("hydration") or {}
    assert hyd.get("request", {}).get("turn_sources") == "cited_turns"
    assert hyd.get("request", {}).get("adjacent_before") == 0
    assert hyd.get("request", {}).get("adjacent_after") == 0

    hydrated = ((out.get("hydration_data") or {}).get("hydrated") or [])
    assert hydrated
    assert "adjacent" not in hydrated[0]


def test_cited_turns_plus_adjacent_includes_neighbors(tmp_path: Path):
    b1, _ = _seed(tmp_path)

    out = trace_request(
        root=tmp_path,
        anchor_ids=[b1],
        hydration={
            "turn_sources": "cited_turns_plus_adjacent",
            "adjacent_before": 1,
            "adjacent_after": 1,
            "max_beads": 5,
        },
    )

    hyd = out.get("hydration") or {}
    assert hyd.get("request", {}).get("turn_sources") == "cited_turns_plus_adjacent"
    assert hyd.get("request", {}).get("adjacent_before") == 1
    assert hyd.get("request", {}).get("adjacent_after") == 1

    hydrated = ((out.get("hydration_data") or {}).get("hydrated") or [])
    assert hydrated
    adj = hydrated[0].get("adjacent") or {}
    assert (adj.get("pivot") or {}).get("turn_id") == "t2"
    assert (adj.get("before") or [{}])[0].get("turn_id") == "t1"
    assert (adj.get("after") or [{}])[0].get("turn_id") == "t3"


def test_hydration_max_beads_is_enforced(tmp_path: Path):
    b1, b2 = _seed(tmp_path)

    out = trace_request(
        root=tmp_path,
        anchor_ids=[b1, b2],
        hydration={
            "turn_sources": "cited_turns",
            "max_beads": 1,
            "adjacent_before": 0,
            "adjacent_after": 0,
        },
    )

    beads = ((out.get("hydration_data") or {}).get("beads") or [])
    assert len(beads) == 1


def test_unsupported_hydration_knobs_are_ignored_without_false_promise(tmp_path: Path):
    b1, _ = _seed(tmp_path)

    out = execute_request(
        root=tmp_path,
        request={
            "raw_query": "alpha",
            "intent": "causal",
            "anchor_ids": [b1],
            "hydration": {
                "turn_sources": "cited_session_transcript",
                "deep_recall": "selected_only",
                "max_turns": 99,
                "max_beads": 3,
            },
        },
        explain=True,
    )

    hyd = out.get("hydration") or {}
    req = hyd.get("request") or {}
    assert req.get("turn_sources") == "cited_turns"
    assert "deep_recall" not in req
    assert "max_turns" not in req
    warns = hyd.get("warnings") or []
    assert "hydration_unsupported_fields_ignored" in warns
