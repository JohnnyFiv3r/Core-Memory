import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.facade

from core_memory.graph.structural import sync_structural_pipeline
from core_memory.graph.core import build_graph
from core_memory.persistence.store import MemoryStore


class TestSyncStructuralPipeline(unittest.TestCase):
    def test_sync_adds_links_from_associations(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).append(
                {"id": "assoc1", "source_bead": a, "target_bead": b, "relationship": "supports", "edge_class": "structural"}
            )
            s._write_json(s.beads_dir / "index.json", idx)

            dry = sync_structural_pipeline(Path(td), apply=False, strict=False)
            self.assertTrue(dry.get("ok"))
            self.assertGreaterEqual(int(dry.get("missing_edge_from_link", 0)), 1)

            app = sync_structural_pipeline(Path(td), apply=True, strict=True)
            self.assertTrue(app.get("ok"))
            self.assertGreaterEqual(int(app.get("links_added", 0)), 1)

    def test_sync_rewrites_inverse_relation_direction(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            blocked = s.add_bead(type="decision", title="Blocked", summary=["blocked"], session_id="main", source_turn_ids=["t1"])
            blocker = s.add_bead(type="evidence", title="Blocker", summary=["blocker"], session_id="main", source_turn_ids=["t1"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).append(
                {
                    "id": "assoc-block",
                    "source_bead": blocked,
                    "target_bead": blocker,
                    "relationship": "blocked_by",
                    "edge_class": "structural",
                }
            )
            s._write_json(s.beads_dir / "index.json", idx)

            app = sync_structural_pipeline(Path(td), apply=True, strict=True)
            self.assertTrue(app.get("ok"))

            idx2 = s._read_json(s.beads_dir / "index.json")
            blocker_links = idx2["beads"][blocker].get("links") or []
            blocked_links = idx2["beads"][blocked].get("links") or []
            self.assertTrue(
                any(
                    isinstance(link, dict)
                    and link.get("source") == "association_sync"
                    and link.get("type") == "blocks"
                    and link.get("bead_id") == blocked
                    for link in blocker_links
                )
            )
            self.assertFalse(
                any(
                    isinstance(link, dict)
                    and link.get("source") == "association_sync"
                    and link.get("type") == "blocks"
                    and link.get("bead_id") == blocker
                    for link in blocked_links
                )
            )

    def test_sync_populates_graph_head(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx["beads"][a].setdefault("links", []).append({"type": "supports", "bead_id": b})
            s._write_json(s.beads_dir / "index.json", idx)

            out = sync_structural_pipeline(Path(td), apply=True, strict=True)
            self.assertTrue(out.get("ok"))
            g = build_graph(Path(td), write_snapshot=False)
            self.assertGreaterEqual(int(g.get("structural_edges", 0)), 1)


if __name__ == "__main__":
    unittest.main()
