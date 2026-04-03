from __future__ import annotations

import json
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.visible_corpus import build_visible_corpus


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_visible_corpus_includes_open_candidate_promoted_archived(tmp_path: Path):
    s = MemoryStore(root=str(tmp_path))
    b_open = s.add_bead(type="context", title="open bead", summary=["a"], session_id="s1", source_turn_ids=["t1"])
    b_cand = s.add_bead(type="context", title="candidate bead", summary=["b"], session_id="s1", source_turn_ids=["t2"])
    b_arch = s.add_bead(type="context", title="archive bead", summary=["c"], session_id="s1", source_turn_ids=["t3"])
    b_prom = s.add_bead(type="context", title="promoted bead", summary=["d"], session_id="s1", source_turn_ids=["t4"])
    b_ineligible = s.add_bead(type="context", title="ineligible", summary=["e"], session_id="s1", source_turn_ids=["t5"])

    idx_path = tmp_path / ".beads" / "index.json"
    idx = _read_json(idx_path)
    idx["beads"][b_open]["status"] = "open"
    idx["beads"][b_cand]["status"] = "candidate"
    idx["beads"][b_arch]["status"] = "archived"
    idx["beads"][b_prom]["status"] = "promoted"
    for bid in [b_open, b_cand, b_arch, b_prom]:
        idx["beads"][bid]["retrieval_eligible"] = True
    idx["beads"][b_ineligible]["retrieval_eligible"] = False
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = build_visible_corpus(tmp_path)
    ids = {r["bead_id"] for r in rows}
    assert b_open in ids and b_cand in ids and b_arch in ids and b_prom in ids
    assert all(r.get("semantic_text") is not None for r in rows)


def test_add_bead_marks_semantic_dirty_and_recall_does_not(tmp_path: Path):
    s = MemoryStore(root=str(tmp_path))
    m_path = tmp_path / ".beads" / "semantic" / "manifest.json"

    s.add_bead(type="decision", title="x", summary=["y"], session_id="s1", source_turn_ids=["t1"])
    m1 = _read_json(m_path)
    assert m1.get("dirty") is True
    first_reason = str(m1.get("last_dirty_reason") or "")
    assert first_reason

    beads = s.query(limit=1)
    assert beads
    m2 = _read_json(m_path)
    # recall/query path should not mutate dirty reason
    assert str(m2.get("last_dirty_reason") or "") == first_reason


def test_link_marks_trace_dirty(tmp_path: Path):
    s = MemoryStore(root=str(tmp_path))
    a = s.add_bead(type="context", title="a", summary=["a"], session_id="s1", source_turn_ids=["t1"])
    b = s.add_bead(type="context", title="b", summary=["b"], session_id="s1", source_turn_ids=["t2"])
    s.link(a, b, "supports", "test")

    p = tmp_path / ".beads" / "events" / "trace-dirty.json"
    state = _read_json(p)
    assert state.get("dirty") is True
    assert state.get("last_dirty_reason") == "link"
