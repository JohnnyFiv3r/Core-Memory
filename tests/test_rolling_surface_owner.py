import json
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore
from core_memory.write_pipeline.consolidate import run_rolling_window_refresh


class TestRollingSurfaceOwner(unittest.TestCase):
    def test_owner_module_metadata(self):
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
            self.assertEqual("core_memory.write_pipeline.rolling_window", (out.get("rolling_window") or {}).get("owner_module"))

            mp = Path(td) / "promoted-context.meta.json"
            payload = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual("core_memory.write_pipeline.rolling_window", ((payload.get("meta") or {}).get("owner_module")))


if __name__ == "__main__":
    unittest.main()
