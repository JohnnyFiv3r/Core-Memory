import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized, process_flush, emit_turn_finalized


class TestFlushCycleGuard(unittest.TestCase):
    def test_flush_skips_when_latest_turn_already_flushed(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="we decided this",
                assistant_final="Decision recorded with context",
            )
            self.assertTrue(out.get("ok"))

            first = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(first.get("ok"))
            self.assertFalse(first.get("skipped", False))

            second = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(second.get("ok"))
            self.assertTrue(second.get("skipped"))
            self.assertEqual("already_flushed_for_latest_done_turn", second.get("reason"))

    def test_flush_anchors_to_latest_done_turn_when_newest_is_pending(self):
        with tempfile.TemporaryDirectory() as td:
            done = process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="we decided this",
                assistant_final="Decision recorded with context",
            )
            self.assertTrue(done.get("ok"))

            # Emit a newer event without processing pass -> pending/unknown barrier state for t2.
            emitted = emit_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t2",
                user_query="new unfinished turn",
                assistant_final="pending processing",
            )
            self.assertTrue(emitted.get("emitted"))

            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            self.assertEqual("t2", out.get("latest_turn_id"))
            self.assertEqual("t1", out.get("latest_done_turn_id"))


if __name__ == "__main__":
    unittest.main()
