import os
import tempfile
import unittest

from core_memory.trigger_orchestrator import run_turn_finalize_pipeline


class TestTriggerOrchestratorBlockMode(unittest.TestCase):
    def test_block_mode_rejects_legacy_shim_calls(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR"] = "1"
            try:
                out = run_turn_finalize_pipeline(
                    root=td,
                    session_id="s1",
                    turn_id="t1",
                    transaction_id="tx1",
                    trace_id="tr1",
                    user_query="remember",
                    assistant_final="Decision",
                )
                self.assertFalse(out.get("ok"))
                self.assertEqual("legacy_trigger_orchestrator_blocked", out.get("error"))
            finally:
                os.environ.pop("CORE_MEMORY_BLOCK_LEGACY_TRIGGER_ORCHESTRATOR", None)


if __name__ == "__main__":
    unittest.main()
