from __future__ import annotations

import json
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import canonical
from core_memory.retrieval.pipeline.canonical import trace_request


def _add(store, *, title, summary, dia, type="evidence"):
    return store.add_bead(
        type=type,
        title=title,
        summary=summary,
        session_id="s1",
        source_turn_ids=[dia],
    )


def _link(idx_path: Path, src: str, dst: str, rel: str = "supports", conf: float = 0.82):
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx.setdefault("associations", []).append(
        {"id": f"a-{src}-{dst}", "source_bead": src, "target_bead": dst, "relationship": rel, "confidence": conf, "status": "active"}
    )
    idx_path.write_text(json.dumps(idx), encoding="utf-8")


def test_traversal_seeds_beyond_top5_and_merges_chain_beads_into_topk(tmp_path: Path, monkeypatch):
    """conv-26 shape: the answer bead is graph-adjacent to a strong anchor but
    semantically weak. The prior top-5 seed + flat-k merge cap could not surface
    it; the wider seed + merge budget must.
    """
    monkeypatch.setenv("CORE_MEMORY_GRAPH_BACKEND", "none")
    store = MemoryStore(root=str(tmp_path))
    idx_path = tmp_path / ".beads" / "index.json"

    # The anchor that semantic search ranks #1 (matches "sunrise painting").
    anchor = _add(store, title="painted lake sunrise last year", summary=["Melanie: I painted that lake sunrise last year!"], dia="D1:14")
    # The GOLD bead carrying the date "2022" — weak semantic match to the query,
    # adjacent to the anchor by a supports edge (one hop).
    gold = _add(store, title="2022 timeframe", summary=["Melanie: that was back in 2022."], dia="D1:12")
    _link(idx_path, anchor, gold)

    # A pile of distractor beads that out-rank the gold bead semantically, so the
    # gold bead lands outside the top-5 anchors (forcing reliance on traversal).
    for i in range(8):
        _add(store, title=f"distractor {i} sunrise scenery", summary=[f"Melanie talks about sunrise scenery {i}."], dia=f"D2:{i}")

    out = trace_request(root=tmp_path, query="When did Melanie paint a sunrise?", anchor_ids=None, k=8)

    result_ids = [r.get("bead_id") for r in (out.get("results") or [])]
    assert gold in result_ids, f"gold bead not surfaced via traversal: {result_ids}"
    gold_row = next(r for r in out["results"] if r.get("bead_id") == gold)
    assert gold_row.get("anchor_reason") == "trace_chain"


def test_chain_merge_budget_exceeds_k(tmp_path: Path, monkeypatch):
    """With k anchors already filling the budget, chain beads must still merge in
    via the separate merge bonus."""
    monkeypatch.setenv("CORE_MEMORY_GRAPH_BACKEND", "none")
    monkeypatch.setenv("CORE_MEMORY_TRACE_CHAIN_MERGE_BONUS", "4")
    store = MemoryStore(root=str(tmp_path))
    idx_path = tmp_path / ".beads" / "index.json"

    anchor = _add(store, title="primary topic alpha", summary=["alpha topic content"], dia="D1:1")
    # k anchors worth of strong semantic matches
    strong = [anchor]
    for i in range(7):
        strong.append(_add(store, title=f"primary topic alpha {i}", summary=[f"alpha topic content {i}"], dia=f"D1:{i+2}"))
    # A chain-only bead, weak semantically, one hop from the anchor.
    chained = _add(store, title="omega edge node", summary=["omega content unrelated lexically"], dia="D9:9")
    _link(idx_path, anchor, chained)

    out = trace_request(root=tmp_path, query="primary topic alpha", anchor_ids=None, k=8)
    result_ids = [r.get("bead_id") for r in (out.get("results") or [])]
    # The merge budget (k=8 + bonus=4) leaves room for the chain bead beyond the
    # 8 semantic anchors.
    assert chained in result_ids


def test_seed_count_and_merge_bonus_are_env_tunable(monkeypatch):
    monkeypatch.setenv("CORE_MEMORY_TRACE_SEED_ANCHORS", "20")
    monkeypatch.setenv("CORE_MEMORY_TRACE_CHAIN_MERGE_BONUS", "5")
    assert canonical._trace_seed_anchors() == 20
    assert canonical._trace_chain_merge_bonus() == 5
    monkeypatch.delenv("CORE_MEMORY_TRACE_SEED_ANCHORS")
    # Falls back to the wider default (was effectively 5 before).
    assert canonical._trace_seed_anchors() == 12


def test_structural_hints_reorder_not_filter(tmp_path: Path, monkeypatch):
    """Soft structural hints reorder matching chains first but never drop chains
    that traversal returned (a wrong/extra hint must not reduce recall)."""
    monkeypatch.setenv("CORE_MEMORY_GRAPH_BACKEND", "none")
    store = MemoryStore(root=str(tmp_path))
    idx_path = tmp_path / ".beads" / "index.json"
    a = _add(store, title="outage root", summary=["the outage happened"], dia="D1:1", type="evidence")
    caused = _add(store, title="db url missing", summary=["db url was missing"], dia="D1:2", type="decision")
    sibling = _add(store, title="rollback decision", summary=["we rolled back the deploy"], dia="D1:3", type="decision")
    _link(idx_path, a, caused, rel="caused_by")
    _link(idx_path, a, sibling, rel="supports")

    # Baseline (no hint): capture which chain beads traversal surfaced.
    base = trace_request(root=tmp_path, query="what caused the outage", anchor_ids=[a], k=8)
    base_ids = {r.get("bead_id") for r in (base.get("results") or [])}

    # With a caused_by hint: the same beads are still present (no filtering), and
    # the caused_by chain is preferred (ordered ahead of the supports chain).
    out = trace_request(
        root=tmp_path,
        query="what caused the outage",
        anchor_ids=[a],
        k=8,
        submission={"structural_hint_relations": ["caused_by"]},
    )
    hinted_ids = {r.get("bead_id") for r in (out.get("results") or [])}
    assert base_ids.issubset(hinted_ids), "hint dropped chains it should only reorder"
    assert caused in hinted_ids
    # caused_by chain ordered before the supports chain when both exist.
    chain_rels = [
        {str((e or {}).get("rel") or "") for e in (c.get("edges") or [])}
        for c in (out.get("chains") or [])
    ]
    if len(chain_rels) >= 2:
        assert "caused_by" in chain_rels[0]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
