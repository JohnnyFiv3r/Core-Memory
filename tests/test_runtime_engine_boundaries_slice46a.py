import unittest
from unittest.mock import patch

from core_memory.runtime import engine


class TestRuntimeEngineBoundariesSlice46A(unittest.TestCase):
    def test_process_turn_finalized_delegates_to_turn_flow(self):
        expected = {"ok": True, "processed": 1}
        with patch.object(engine, "process_turn_finalized_impl", return_value=expected) as spy:
            out = engine.process_turn_finalized(
                root=".",
                session_id="s1",
                turn_id="t1",
                user_query="q",
                assistant_final="a",
            )

        self.assertEqual(expected, out)
        self.assertEqual(1, spy.call_count)

    def test_process_session_start_delegates_to_session_start_flow(self):
        expected = {"ok": True, "created": False, "bead_id": "bead-x"}
        with patch.object(engine, "process_session_start_impl", return_value=expected) as spy:
            out = engine.process_session_start(root=".", session_id="s1", source="test", max_items=10)

        self.assertEqual(expected, out)
        spy.assert_called_once_with(root=".", session_id="s1", source="test", max_items=10)

    def test_process_flush_delegates_to_flush_flow(self):
        expected = {"ok": True, "flush_tx_id": "fx-1"}
        with patch.object(engine, "process_flush_impl", return_value=expected) as spy:
            out = engine.process_flush(
                root=".",
                session_id="s1",
                promote=False,
                token_budget=100,
                max_beads=20,
                source="test",
                flush_tx_id="fx-1",
            )

        self.assertEqual(expected, out)
        spy.assert_called_once_with(
            root=".",
            session_id="s1",
            promote=False,
            token_budget=100,
            max_beads=20,
            source="test",
            flush_tx_id="fx-1",
        )


if __name__ == "__main__":
    unittest.main()
