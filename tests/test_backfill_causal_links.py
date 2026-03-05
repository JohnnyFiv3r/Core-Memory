import tempfile
import unittest
from pathlib import Path

from core_memory.graph import backfill_causal_links, build_graph
from core_memory.store import MemoryStore


class TestBackfillCausalLinks(unittest.TestCase):
    def test_backfill_proposes_and_applies_links(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Candidate promotion policy", summary=["candidate only promotion"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Promotion inflation evidence", summary=["candidate promotion issue"], session_id="main", source_turn_ids=["t1"])

            dry = backfill_causal_links(Path(td), apply=False, min_overlap=1)
            self.assertTrue(dry.get("ok"))
            self.assertGreaterEqual(int(dry.get("proposed", 0)), 1)

            app = backfill_causal_links(Path(td), apply=True, min_overlap=1)
            self.assertTrue(app.get("ok"))
            self.assertGreaterEqual(int(app.get("links_added", 0)), 1)
            g = build_graph(Path(td), write_snapshot=False)
            self.assertGreaterEqual(int(g.get("structural_edges", 0)), 1)


if __name__ == "__main__":
    unittest.main()
