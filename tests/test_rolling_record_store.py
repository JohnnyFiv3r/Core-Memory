import json
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.write_pipeline.consolidate import run_rolling_window_refresh
from core_memory.rolling_record_store import read_rolling_records


class TestRollingRecordStore(unittest.TestCase):
    def test_record_store_written_and_readable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(type="context", title="A", summary=["one"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="B", summary=["two"], session_id="main", source_turn_ids=["t2"])

            out = run_rolling_window_refresh(
                root=str(root),
                workspace_root=td,
                token_budget=500,
                max_beads=20,
            )
            self.assertTrue(out.get("ok"))

            rp = Path(td) / "rolling-window.records.json"
            self.assertTrue(rp.exists())
            payload = json.loads(rp.read_text(encoding="utf-8"))
            self.assertEqual("rolling_window_record_store", payload.get("surface"))
            self.assertGreaterEqual(len(payload.get("records") or []), 1)

            reread = read_rolling_records(td)
            self.assertEqual("rolling_window_record_store", reread.get("surface"))
            self.assertGreaterEqual(len(reread.get("records") or []), 1)


if __name__ == "__main__":
    unittest.main()
