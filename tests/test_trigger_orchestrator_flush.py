import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.trigger_orchestrator import run_flush_pipeline


class TestTriggerOrchestratorFlush(unittest.TestCase):
    def test_run_flush_pipeline_writes_checkpoints_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="t", summary=["x"], session_id="main", source_turn_ids=["t1"])

            out = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=500,
                max_beads=20,
                source="admin_cli",
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("flush_tx_id"))

            ck = s.beads_dir / "events" / "flush-checkpoints.jsonl"
            self.assertTrue(ck.exists())
            text = ck.read_text(encoding="utf-8")
            self.assertIn('"stage": "start"', text)
            self.assertIn('"session_surface": "session_file"', text)
            self.assertIn('"stage": "committed"', text)


if __name__ == "__main__":
    unittest.main()
