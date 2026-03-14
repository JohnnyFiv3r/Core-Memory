import tempfile
import unittest

from core_memory.openclaw_integration import finalize_and_process_turn, process_pending_memory_events
from core_memory.event_worker import SidecarPolicy
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.store import MemoryStore


class TestV2P5CanonicalPathEnforcement(unittest.TestCase):
    def test_canonical_turn_path_is_default_authority(self):
        with tempfile.TemporaryDirectory() as td:
            out = finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: canonical path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))

    def test_legacy_poller_cannot_double_process_canonical_done_turn(self):
        with tempfile.TemporaryDirectory() as td:
            finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: canonical path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            out = process_pending_memory_events(td, max_events=10)
            self.assertEqual("legacy_sidecar_compat", out.get("authority_path"))
            self.assertEqual(0, out.get("processed"))

    def test_memory_execute_contract_stable_keys(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.execute(
                {
                    "raw_query": "remember decision A",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            for k in ["results", "chains", "confidence", "next_action", "source_surface", "source_scope"]:
                self.assertIn(k, out)


if __name__ == "__main__":
    unittest.main()
