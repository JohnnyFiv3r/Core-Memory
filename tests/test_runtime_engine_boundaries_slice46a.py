import unittest
from unittest.mock import patch

from core_memory.runtime import engine


class TestRuntimeEngineBoundariesSlice46A(unittest.TestCase):
    def test_process_session_start_delegates_to_session_start_flow(self):
        expected = {"ok": True, "created": False, "bead_id": "bead-x"}
        with patch.object(engine, "process_session_start_impl", return_value=expected) as spy:
            out = engine.process_session_start(root=".", session_id="s1", source="test", max_items=10)

        self.assertEqual(expected, out)
        spy.assert_called_once_with(root=".", session_id="s1", source="test", max_items=10)


if __name__ == "__main__":
    unittest.main()
