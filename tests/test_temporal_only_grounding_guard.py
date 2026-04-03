import tempfile
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import execute_request as canonical_execute_request
from core_memory.retrieval.pipeline.canonical import trace_request
from core_memory.retrieval.pipeline.execute import execute_request as legacy_execute_request


def test_trace_follows_only_is_not_full_grounding():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
        b = s.add_bead(type="context", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
        s.link(a, b, "follows", "temporal")

        out = trace_request(root=td, anchor_ids=[a], intent="causal", query="", k=5)
        g = out.get("grounding") or {}
        assert g.get("level") == "partial"
        assert g.get("reason") == "non_temporal_structural_missing"


def test_trace_non_temporal_structural_edge_allows_full_grounding():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
        b = s.add_bead(type="evidence", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
        s.link(a, b, "supports", "causal")

        out = trace_request(root=td, anchor_ids=[a], intent="causal", query="", k=5)
        g = out.get("grounding") or {}
        assert g.get("level") == "full"
        assert g.get("reason") == "grounded"


def test_trace_associated_with_only_is_not_full_grounding():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
        b = s.add_bead(type="context", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
        s.link(a, b, "associated_with", "weak relation")

        out = trace_request(root=td, anchor_ids=[a], intent="causal", query="", k=5)
        g = out.get("grounding") or {}
        assert g.get("level") == "partial"
        assert g.get("reason") == "non_temporal_structural_missing"


def test_canonical_execute_respects_temporal_only_guard():
    with tempfile.TemporaryDirectory() as td:
        s = MemoryStore(td)
        a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
        b = s.add_bead(type="context", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
        s.link(a, b, "follows", "temporal")

        out = canonical_execute_request(
            root=td,
            request={
                "raw_query": "why",
                "intent": "causal",
                "grounding_mode": "require_grounded",
                "anchor_ids": [a],
                "k": 5,
            },
            explain=False,
        )
        g = out.get("grounding") or {}
        assert g.get("level") == "partial"
        assert g.get("reason") == "non_temporal_structural_missing"


@patch("core_memory.retrieval.pipeline.execute._load_beads")
@patch("core_memory.retrieval.pipeline.execute.search_typed")
@patch("core_memory.retrieval.pipeline.execute.snap_form")
@patch("core_memory.retrieval.pipeline.execute.build_catalog")
def test_legacy_execute_marks_temporal_only_as_not_grounded(mcat, msnap, msearch, mbeads):
    mcat.return_value = {}
    msnap.return_value = {"snapped": {"intent": "causal", "query_text": "q"}, "decisions": {}}
    mbeads.return_value = {}
    msearch.return_value = {
        "ok": True,
        "results": [{"bead_id": "b1", "title": "A", "type": "decision", "snippet": "", "score": 0.9, "source_surface": "session_bead"}],
        "chains": [{"path": ["b1", "b2"], "edges": [{"rel": "follows", "class": "structural"}], "score": 0.8}],
        "snapped_query": {"intent": "causal", "query_text": "q"},
        "warnings": [],
    }

    with tempfile.TemporaryDirectory() as td:
        out = legacy_execute_request({"raw_query": "q", "intent": "causal", "k": 5}, root=td, explain=False)

    g = out.get("grounding") or {}
    assert g.get("achieved") is False
    assert g.get("reason") == "no_structural_edges_found"
