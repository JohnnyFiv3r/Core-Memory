import json
import tempfile
import unittest
from pathlib import Path

from core_memory.memory_engine import continuity_injection_context
from core_memory.store import MemoryStore
from core_memory.write_pipeline.consolidate import run_rolling_window_refresh


class TestContinuityInjectionAuthority(unittest.TestCase):
    def test_uses_rolling_record_store_as_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(type="context", title="A", summary=["one"], session_id="main", source_turn_ids=["t1"])

            run_rolling_window_refresh(root=str(root), workspace_root=td, token_budget=500, max_beads=20)

            out = continuity_injection_context(workspace_root=td, max_items=20)
            self.assertEqual("rolling_record_store", out.get("authority"))
            self.assertEqual("continuity_injection_context", (out.get("engine") or {}).get("entry"))
            self.assertGreaterEqual(len(out.get("records") or []), 1)

    def test_fallback_to_meta_when_records_missing(self):
        with tempfile.TemporaryDirectory() as td:
            mp = Path(td) / "promoted-context.meta.json"
            mp.write_text(json.dumps({"included_bead_ids": ["b1"], "meta": {"surface": "rolling_window"}}), encoding="utf-8")

            out = continuity_injection_context(workspace_root=td)
            self.assertEqual("promoted_context_meta_fallback", out.get("authority"))
            self.assertEqual(["b1"], out.get("included_bead_ids"))


if __name__ == "__main__":
    unittest.main()
