import os
import tempfile
import unittest

from core_memory.openclaw_integration import finalize_and_process_turn
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.store import MemoryStore
from core_memory.trigger_orchestrator import run_flush_pipeline


class TestTriggerOrchestratorFlushRecovery(unittest.TestCase):
    def test_induced_failure_then_retry_same_tx_commits(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="t", summary=["x"], session_id="main", source_turn_ids=["t1"])
            finalize_and_process_turn(
                root=td,
                session_id="main",
                turn_id="t_done",
                transaction_id="tx_done",
                trace_id="tr_done",
                user_query="remember this",
                assistant_final="Decision: processed",
                policy=SidecarPolicy(create_threshold=0.6),
            )

            old = os.environ.get("CORE_MEMORY_FLUSH_FAIL_STAGE")
            try:
                os.environ["CORE_MEMORY_FLUSH_FAIL_STAGE"] = "before_commit"
                out1 = run_flush_pipeline(
                    root=td,
                    session_id="main",
                    promote=False,
                    token_budget=500,
                    max_beads=20,
                    source="flush_hook",
                    flush_tx_id="tx-recover-1",
                )
                self.assertFalse(out1.get("ok"))
                self.assertEqual("induced_failure_before_commit", out1.get("error"))

                os.environ.pop("CORE_MEMORY_FLUSH_FAIL_STAGE", None)
                out2 = run_flush_pipeline(
                    root=td,
                    session_id="main",
                    promote=False,
                    token_budget=500,
                    max_beads=20,
                    source="flush_hook",
                    flush_tx_id="tx-recover-1",
                )
                self.assertTrue(out2.get("ok"))
                self.assertEqual("canonical_in_process", out2.get("authority_path"))
            finally:
                if old is None:
                    os.environ.pop("CORE_MEMORY_FLUSH_FAIL_STAGE", None)
                else:
                    os.environ["CORE_MEMORY_FLUSH_FAIL_STAGE"] = old


if __name__ == "__main__":
    unittest.main()
