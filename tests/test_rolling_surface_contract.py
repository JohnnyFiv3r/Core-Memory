import json
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.write_pipeline.consolidate import run_rolling_window_refresh


class TestRollingSurfaceContract(unittest.TestCase):
    def test_rolling_surface_metadata_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(type="context", title="A", summary=["one"], session_id="main", source_turn_ids=["t1"])

            out = run_rolling_window_refresh(
                root=str(root),
                workspace_root=td,
                token_budget=500,
                max_beads=20,
            )
            self.assertTrue(out.get("ok"))

            mp = Path(td) / "promoted-context.meta.json"
            self.assertTrue(mp.exists())
            meta = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual("rolling_window", meta.get("surface"))
            self.assertIn("meta", meta)
            self.assertIn("included_bead_ids", meta)
            self.assertIn("excluded_bead_ids", meta)


if __name__ == "__main__":
    unittest.main()
