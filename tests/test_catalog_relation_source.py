import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
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

    def test_public_catalog_hides_boundary_bead_types(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="D", summary=["x"], session_id="s", source_turn_ids=["t1"])

            cat = build_catalog(s.root)
            bead_types = set(cat.get("bead_types") or [])
            self.assertIn("decision", bead_types)
            self.assertNotIn("session_start", bead_types)
            self.assertNotIn("session_end", bead_types)

    def test_relation_types_are_canonicalized_in_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s", source_turn_ids=["t2"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).extend(
                [
                    {
                        "id": "assoc-causes",
                        "source_bead": b1,
                        "target_bead": b2,
                        "relationship": "Causes",
                    },
                    {
                        "id": "assoc-unknown",
                        "source_bead": b1,
                        "target_bead": b2,
                        "relationship": "mystery_rel",
                    },
                ]
            )
            s._write_json(s.beads_dir / "index.json", idx)

            cat = build_catalog(s.root)
            rels = set(cat.get("relation_types") or [])
            self.assertIn("caused_by", rels)
            self.assertNotIn("mystery_rel", rels)


if __name__ == "__main__":
    unittest.main()
