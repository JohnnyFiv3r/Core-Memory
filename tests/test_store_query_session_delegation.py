from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreQuerySessionDelegationSlice73A(unittest.TestCase):
    def test_query_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-query-deleg-") as td:
            store = MemoryStore(td)
            expected = [{"id": "bead-1"}]
            with patch("core_memory.persistence.store_query.query_for_store", return_value=expected) as stub:
                out = store.query(type="decision", limit=3, session_id="s1")

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("decision", kwargs.get("type"))
            self.assertEqual(3, kwargs.get("limit"))
            self.assertEqual("s1", kwargs.get("session_id"))

    def test_capture_turn_and_consolidate_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-session-deleg-") as td:
            store = MemoryStore(td)
            with patch("core_memory.persistence.store_session_ops.capture_turn_for_store", return_value=None) as stub_turn:
                store.capture_turn(role="assistant", content="hi", session_id="s1")
            self.assertEqual(1, stub_turn.call_count)
            t_args, t_kwargs = stub_turn.call_args
            self.assertIs(t_args[0], store)
            self.assertEqual("assistant", t_kwargs.get("role"))
            self.assertEqual("s1", t_kwargs.get("session_id"))

            expected = {"session_id": "s1", "turns": 1, "events": 0, "end_bead": "bead-xyz"}
            with patch("core_memory.persistence.store_session_ops.consolidate_for_store", return_value=expected) as stub_cons:
                out = store.consolidate(session_id="s1")
            self.assertEqual(expected, out)
            self.assertEqual(1, stub_cons.call_count)
            c_args, c_kwargs = stub_cons.call_args
            self.assertIs(c_args[0], store)
            self.assertEqual("s1", c_kwargs.get("session_id"))


if __name__ == "__main__":
    unittest.main()
