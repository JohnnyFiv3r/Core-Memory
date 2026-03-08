import tempfile
import unittest

from core_memory.trigger_orchestrator import run_turn_finalize_pipeline
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.store import MemoryStore


class TestTriggerOrchestrator(unittest.TestCase):
    def test_run_turn_finalize_pipeline_processes_once(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = run_turn_finalize_pipeline(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this decision",
                assistant_final="Decision: keep canonical trigger path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out1.get("ok"))
            self.assertEqual(1, out1.get("processed"))

            # idempotent replay should not process again
            out2 = run_turn_finalize_pipeline(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this decision",
                assistant_final="Decision: keep canonical trigger path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out2.get("ok"))
            self.assertEqual(0, out2.get("processed"))

            s = MemoryStore(td)
            self.assertGreaterEqual(s.stats().get("total_beads", 0), 1)


if __name__ == "__main__":
    unittest.main()
