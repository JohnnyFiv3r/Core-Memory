import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.trigger_orchestrator import run_flush_pipeline
from core_memory.sidecar_hook import maybe_emit_finalize_memory_event
from core_memory.openclaw_integration import finalize_and_process_turn
from core_memory.sidecar_worker import SidecarPolicy


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

    def test_flush_barrier_fails_when_latest_turn_not_processed(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="t", summary=["x"], session_id="main", source_turn_ids=["t1"])

            maybe_emit_finalize_memory_event(
                td,
                session_id="main",
                turn_id="t_pending",
                transaction_id="tx_pending",
                trace_id="tr_pending",
                user_query="remember this pending turn",
                assistant_final="pending turn output",
                trace_depth=0,
                origin="USER_TURN",
            )

            out = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=500,
                max_beads=20,
                source="flush_hook",
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("enrichment_barrier_not_satisfied", out.get("error"))

    def test_flush_barrier_passes_after_turn_processed(self):
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

    def test_flush_replay_same_tx_is_idempotent_skip(self):
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

            out1 = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=500,
                max_beads=20,
                source="flush_hook",
                flush_tx_id="tx-fixed-1",
            )
            out2 = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=500,
                max_beads=20,
                source="flush_hook",
                flush_tx_id="tx-fixed-1",
            )
            self.assertTrue(out1.get("ok"))
            self.assertTrue(out2.get("ok"))
            self.assertTrue(out2.get("skipped"))
            self.assertEqual("already_committed", out2.get("reason"))


if __name__ == "__main__":
    unittest.main()
