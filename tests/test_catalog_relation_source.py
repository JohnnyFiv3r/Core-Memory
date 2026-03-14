import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.retrieval.pipeline.catalog import build_catalog


class TestCatalogRelationSource(unittest.TestCase):
    def test_relation_types_from_associations(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s", source_turn_ids=["t2"])

            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).append(
                {
                    "id": "assoc-test-1",
                    "source_bead": b1,
                    "target_bead": b2,
                    "relationship": "supports",
                }
            )
            s._write_json(s.beads_dir / "index.json", idx)

            cat = build_catalog(s.root)
            self.assertIn("supports", cat.get("relation_types") or [])


if __name__ == "__main__":
    unittest.main()
