import tempfile
import unittest

from core_memory.memory_engine import process_turn_finalized, process_flush


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
            self.assertEqual("already_flushed_for_latest_turn", second.get("reason"))


if __name__ == "__main__":
    unittest.main()
