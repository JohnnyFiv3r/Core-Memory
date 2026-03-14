import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.write_pipeline.rolling_window import build_rolling_surface as build_rolling_window


class TestRollingFifoDeterminism(unittest.TestCase):
    def test_rolling_fifo_recency_order(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="old", summary=["a"], session_id="main", source_turn_ids=["t1"], created_at="2026-01-01T00:00:00+00:00")
            s.add_bead(type="context", title="mid", summary=["b"], session_id="main", source_turn_ids=["t2"], created_at="2026-01-02T00:00:00+00:00")
            s.add_bead(type="context", title="new", summary=["c"], session_id="main", source_turn_ids=["t3"], created_at="2026-01-03T00:00:00+00:00")

            _, _, included_ids, _ = build_rolling_window(td, token_budget=9999, max_beads=3)
            idx = s._read_json(s.beads_dir / "index.json")["beads"]
            titles = [idx[i]["title"] for i in included_ids]
            self.assertEqual(["new", "mid", "old"], titles)

    def test_token_budget_cutoff_is_strict_fifo(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            # newest: medium size
            s.add_bead(type="context", title="new", summary=["x" * 120], session_id="main", source_turn_ids=["t1"], created_at="2026-01-03T00:00:00+00:00")
            # next: large enough to exceed remaining budget
            s.add_bead(type="context", title="mid", summary=["y" * 800], session_id="main", source_turn_ids=["t2"], created_at="2026-01-02T00:00:00+00:00")
            # older: tiny (would fit if non-strict skip-continue were used)
            s.add_bead(type="context", title="old", summary=["z"], session_id="main", source_turn_ids=["t3"], created_at="2026-01-01T00:00:00+00:00")

            _, _, included_ids, _ = build_rolling_window(td, token_budget=120, max_beads=10)
            idx = s._read_json(s.beads_dir / "index.json")["beads"]
            titles = [idx[i]["title"] for i in included_ids]
            self.assertEqual(["new"], titles)

    def test_rebuild_stability(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            for i in range(6):
                s.add_bead(
                    type="context",
                    title=f"b{i}",
                    summary=["v" * (30 + i)],
                    session_id="main",
                    source_turn_ids=[f"t{i}"],
                    created_at=f"2026-01-{i+1:02d}T00:00:00+00:00",
                )

            t1, m1, in1, ex1 = build_rolling_window(td, token_budget=180, max_beads=6)
            t2, m2, in2, ex2 = build_rolling_window(td, token_budget=180, max_beads=6)

            self.assertEqual(in1, in2)
            self.assertEqual(ex1, ex2)
            self.assertEqual(m1, m2)
            self.assertEqual(t1, t2)


if __name__ == "__main__":
    unittest.main()
