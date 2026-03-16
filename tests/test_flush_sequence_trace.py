import tempfile
import unittest

from core_memory.runtime.engine import process_turn_finalized, process_flush


class TestFlushSequenceTrace(unittest.TestCase):
    def test_flush_returns_sequence_trace(self):
        with tempfile.TemporaryDirectory() as td:
            process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="capture this decision",
                assistant_final="Decision captured with rationale",
            )
            out = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(out.get("ok"))
            result = out.get("result") or {}
            self.assertTrue(result.get("sequence_ok"))
            self.assertEqual(
                ["archive_compact_session", "rolling_window_write", "archive_compact_historical"],
                result.get("phase_trace"),
            )


if __name__ == "__main__":
    unittest.main()
