import json
import tempfile
import unittest
from pathlib import Path

from core_memory.graph import backfill_structural_edges, build_graph, infer_structural_edges
from core_memory.store import MemoryStore


class TestStructuralInferenceHardening(unittest.TestCase):
    def test_backfill_skips_derived_association_edges(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["a"], status="candidate", session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="lesson", title="B", summary=["b"], status="candidate", session_id="main", source_turn_ids=["t1"])

            idx = s._read_json(s.beads_dir / "index.json")
            idx.setdefault("associations", []).append(
                {
                    "id": "assoc-1",
                    "type": "association",
                    "source_bead": a,
                    "target_bead": b,
                    "relationship": "supports",
                    "edge_class": "derived",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )
            s._write_json(s.beads_dir / "index.json", idx)

            out = backfill_structural_edges(Path(td))
            self.assertEqual(0, out.get("added"))

    def test_infer_structural_respects_gates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="D", summary=["d"], status="candidate", session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="E", summary=["e"], status="open", session_id="main", source_turn_ids=["t1"])

            dry = infer_structural_edges(Path(td), min_confidence=0.9, apply=False)
            self.assertGreaterEqual(dry.get("candidates", 0), 1)
            app = infer_structural_edges(Path(td), min_confidence=0.9, apply=True)
            self.assertGreaterEqual(app.get("applied", 0), 1)

            g = build_graph(Path(td), write_snapshot=False)
            self.assertGreaterEqual(int(g.get("structural_edges", 0)), 1)


if __name__ == "__main__":
    unittest.main()
