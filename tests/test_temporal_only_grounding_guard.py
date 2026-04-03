import tempfile

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import execute_request as canonical_execute_request
from core_memory.retrieval.pipeline.canonical import trace_request


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

