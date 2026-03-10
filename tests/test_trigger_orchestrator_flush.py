import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.trigger_orchestrator import run_flush_pipeline
from core_memory.openclaw_integration import finalize_and_process_turn
from core_memory.event_worker import SidecarPolicy


class TestTriggerOrchestratorFlush(unittest.TestCase):
    def test_run_flush_pipeline_delegates_to_engine(self):
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

            out = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=500,
                max_beads=20,
                source="flush_hook",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertEqual("core_memory.memory_engine", ((out.get("shim") or {}).get("delegated_to")))


if __name__ == "__main__":
    unittest.main()
