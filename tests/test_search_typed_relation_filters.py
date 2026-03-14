import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_typed


class TestSearchTypedRelationFilters(unittest.TestCase):
    def test_relation_filter_applies_to_chains(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["anchor"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="B", summary=["support"], session_id="main", source_turn_ids=["t2"])
            c = s.add_bead(type="outcome", title="C", summary=["result"], session_id="main", source_turn_ids=["t3"])
            idx = s._read_json(s.beads_dir / "index.json")
            beads = idx.get("beads") or {}
            beads[a]["links"] = [{"bead_id": b, "type": "supports"}, {"bead_id": c, "type": "derived_from"}]
            s._write_json(s.beads_dir / "index.json", idx)

            out = memory_search_typed(td, {
                "intent": "causal",
                "query_text": "anchor",
                "k": 5,
                "require_structural": True,
                "relation_types": ["supports"],
            }, explain=True)
            self.assertTrue(out.get("ok"))
            chains = out.get("chains") or []
            if chains:
                for ch in chains:
                    rels = {str(e.get("rel") or "") for e in (ch.get("edges") or [])}
                    self.assertIn("supports", rels)


if __name__ == "__main__":
    unittest.main()
