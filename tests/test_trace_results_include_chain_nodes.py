from __future__ import annotations

import json
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import trace_request


def test_trace_request_returns_expanded_chain_nodes_not_only_anchors(tmp_path: Path):
    store = MemoryStore(root=str(tmp_path))
    anchor = store.add_bead(
        type="evidence",
        title="Melanie pottery",
        summary=["Melanie does pottery."],
        session_id="s1",
        source_turn_ids=["D1:1"],
    )
    sibling = store.add_bead(
        type="decision",
        title="Melanie camping",
        summary=["Melanie also goes camping."],
        session_id="s1",
        source_turn_ids=["D2:2"],
    )

    idx_path = tmp_path / ".beads" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx.setdefault("associations", []).append(
        {
            "id": "assoc-test",
            "source_bead": anchor,
            "target_bead": sibling,
            "relationship": "supports",
            "confidence": 0.9,
            "status": "active",
        }
    )
    idx_path.write_text(json.dumps(idx), encoding="utf-8")

    out = trace_request(root=tmp_path, query="What activities does Melanie do?", anchor_ids=[anchor], k=5)

    assert out["chains"]
    result_ids = [row.get("bead_id") for row in out.get("results") or []]
    assert result_ids[0] == anchor
    assert sibling in result_ids
    expanded = next(row for row in out["results"] if row.get("bead_id") == sibling)
    assert expanded.get("anchor_reason") == "trace_chain"
    assert expanded.get("source_surface") == "causal_trace"
    assert (out.get("trace_diagnostics") or {}).get("assoc_edges_after_conf_floor") == 1
