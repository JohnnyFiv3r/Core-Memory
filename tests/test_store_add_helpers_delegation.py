from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreAddHelpersDelegationSlice69A(unittest.TestCase):
    def test_resolve_bead_session_id_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-add-deleg-") as td:
            store = MemoryStore(td)
            with patch(
                "core_memory.persistence.store_add_helpers.resolve_bead_session_id_for_store",
                return_value="session-delegated",
            ) as stub:
                out = store._resolve_bead_session_id(session_id=None, source_turn_ids=["t1"])

            self.assertEqual("session-delegated", out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertIsNone(kwargs.get("session_id"))
            self.assertEqual(["t1"], kwargs.get("source_turn_ids"))

    def test_duplicate_detection_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-add-deleg-") as td:
            store = MemoryStore(td)
            index = {"beads": {}}
            bead = {"id": "bead-new"}
            with patch(
                "core_memory.persistence.store_add_helpers.find_recent_duplicate_bead_id_for_store",
                return_value="bead-dup",
            ) as stub:
                out = store._find_recent_duplicate_bead_id(index, bead, session_id="s1", window=10)

            self.assertEqual("bead-dup", out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertIs(args[1], index)
            self.assertIs(args[2], bead)
            self.assertEqual("s1", kwargs.get("session_id"))
            self.assertEqual(10, kwargs.get("window"))


if __name__ == "__main__":
    unittest.main()
