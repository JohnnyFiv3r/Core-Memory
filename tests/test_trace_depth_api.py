from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.pipeline.canonical import trace_request
from core_memory.retrieval.tools.memory import trace as memory_trace_tool


def _anchors():
    return {
        "ok": True,
        "anchors": [{"bead_id": "b1", "title": "A", "type": "context", "score": 1.0}],
        "results": [{"bead_id": "b1", "title": "A", "type": "context", "score": 1.0}],
        "warnings": [],
        "confidence": "medium",
        "next_action": "answer",
        "snapped": {"raw_query": "why", "intent": "causal", "k": 1},
    }


def test_trace_request_honors_explicit_max_depth(tmp_path: Path):
    with patch("core_memory.retrieval.pipeline.canonical.get_backend_capabilities") as caps, \
         patch("core_memory.retrieval.pipeline.canonical.search_request", return_value=_anchors()), \
         patch("core_memory.retrieval.pipeline.canonical.causal_traverse", return_value={"ok": True, "chains": [], "assoc_diag": {}}) as trav:
        caps.return_value.graph_traversal = False
        out = trace_request(root=tmp_path, query="why", k=1, max_depth=6, max_chains=9)

    trav.assert_called_once()
    assert trav.call_args.kwargs["max_depth"] == 6
    assert trav.call_args.kwargs["max_chains"] == 9
    assert out["trace_diagnostics"]["max_depth"] == 6
    assert out["trace_diagnostics"]["max_chains"] == 9
    assert out["trace_diagnostics"]["requested_max_depth"] == "6"


def test_trace_request_defaults_to_six_hop_cap(tmp_path: Path):
    with patch("core_memory.retrieval.pipeline.canonical.get_backend_capabilities") as caps, \
         patch("core_memory.retrieval.pipeline.canonical.search_request", return_value=_anchors()), \
         patch("core_memory.retrieval.pipeline.canonical.causal_traverse", return_value={"ok": True, "chains": [], "assoc_diag": {}}) as trav:
        caps.return_value.graph_traversal = False
        out = trace_request(root=tmp_path, query="why", k=1)

    trav.assert_called_once()
    assert trav.call_args.kwargs["max_depth"] == 6
    assert out["trace_diagnostics"]["max_depth"] == 6
    assert out["trace_diagnostics"]["requested_max_depth"] is None


def test_public_memory_trace_tool_accepts_max_depth(tmp_path: Path):
    with patch("core_memory.retrieval.pipeline.canonical.get_backend_capabilities") as caps, \
         patch("core_memory.retrieval.pipeline.canonical.search_request", return_value=_anchors()), \
         patch("core_memory.retrieval.pipeline.canonical.causal_traverse", return_value={"ok": True, "chains": [], "assoc_diag": {}}) as trav:
        caps.return_value.graph_traversal = False
        out = memory_trace_tool(query="why", root=str(tmp_path), k=1, max_depth=6)

    trav.assert_called_once()
    assert trav.call_args.kwargs["max_depth"] == 6
    assert out["trace_diagnostics"]["max_depth"] == 6
