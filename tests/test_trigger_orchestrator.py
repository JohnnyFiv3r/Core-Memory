import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.worker import SidecarPolicy
from core_memory.persistence.store import MemoryStore


class TestTriggerOrchestrator(unittest.TestCase):
    def test_process_turn_finalized_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = process_turn_finalized(
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
            self.assertEqual("canonical_in_process", out1.get("authority_path"))

            out2 = process_turn_finalized(
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
