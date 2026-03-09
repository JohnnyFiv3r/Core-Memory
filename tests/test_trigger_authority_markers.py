import tempfile
import unittest

from core_memory.openclaw_integration import process_pending_memory_events, finalize_and_process_turn
from core_memory.sidecar_worker import SidecarPolicy


class TestTriggerAuthorityMarkers(unittest.TestCase):
    def test_canonical_turn_path_marker(self):
        with tempfile.TemporaryDirectory() as td:
            out = finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: use canonical path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertEqual("canonical_in_process", out.get("authority_path"))

    def test_legacy_poller_marker(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_pending_memory_events(td, max_events=5)
            self.assertEqual("legacy_sidecar_compat", out.get("authority_path"))


if __name__ == "__main__":
    unittest.main()
