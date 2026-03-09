import os
import tempfile
import unittest

from core_memory.openclaw_integration import process_pending_memory_events
from core_memory.sidecar_hook import maybe_emit_finalize_memory_event


class TestLegacyPollerFence(unittest.TestCase):
    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_pending_memory_events(td, max_events=5)
            self.assertTrue(out.get("skipped"))
            self.assertEqual("legacy_poller_disabled", out.get("reason"))

    def test_enabled_via_env(self):
        old = os.environ.get("CORE_MEMORY_ENABLE_LEGACY_POLLER")
        try:
            os.environ["CORE_MEMORY_ENABLE_LEGACY_POLLER"] = "1"
            with tempfile.TemporaryDirectory() as td:
                maybe_emit_finalize_memory_event(
                    td,
                    session_id="s1",
                    turn_id="t1",
                    transaction_id="tx1",
                    trace_id="tr1",
                    user_query="remember this",
                    assistant_final="Decision: legacy path",
                    trace_depth=0,
                    origin="USER_TURN",
                )
                out = process_pending_memory_events(td, max_events=5)
                self.assertIn("processed", out)
                self.assertFalse(out.get("skipped", False))
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_ENABLE_LEGACY_POLLER", None)
            else:
                os.environ["CORE_MEMORY_ENABLE_LEGACY_POLLER"] = old


if __name__ == "__main__":
    unittest.main()
