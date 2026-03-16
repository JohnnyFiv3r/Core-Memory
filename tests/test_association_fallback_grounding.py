import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestAssociationFallbackGrounding(unittest.TestCase):
    def test_association_rows_can_provide_radius1_structural_chain(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Decision", summary=["x"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Evidence", summary=["y"], session_id="main", source_turn_ids=["t1"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).append(
                {
                    "id": "assoc-test",
                    "source_bead": d,
                    "target_bead": e,
                    "relationship": "supports",
                    "edge_class": "structural",
                }
            )
            s._write_json(s.beads_dir / "index.json", idx)

            out = memory_reason("why decision", root=td)
            self.assertTrue(out.get("ok"))
            self.assertTrue(any(len(c.get("edges") or []) > 0 for c in (out.get("chains") or [])))


if __name__ == "__main__":
    unittest.main()
