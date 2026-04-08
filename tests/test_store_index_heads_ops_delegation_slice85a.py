from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreIndexHeadsOpsDelegationSlice85A(unittest.TestCase):
    def test_heads_methods_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-heads-deleg-") as td:
            store = MemoryStore(td)

            expected_heads = {"topics": {}, "goals": {}, "updated_at": "x"}
            with patch("core_memory.persistence.store_index_heads_ops.read_heads_for_store", return_value=expected_heads) as stub_read:
                out = store._read_heads()
            self.assertEqual(expected_heads, out)
            self.assertEqual(1, stub_read.call_count)
            self.assertIs(stub_read.call_args[0][0], store)

            with patch("core_memory.persistence.store_index_heads_ops.write_heads_for_store", return_value=None) as stub_write:
                store._write_heads({"topics": {}, "goals": {}})
            self.assertEqual(1, stub_write.call_count)
            self.assertIs(stub_write.call_args[0][0], store)

            with patch(
                "core_memory.persistence.store_index_heads_ops.update_heads_for_bead_for_store",
                return_value={"topics": {"t": {"bead_id": "b"}}, "goals": {}},
            ) as stub_update:
                out2 = store._update_heads_for_bead({"topics": {}, "goals": {}}, {"id": "b", "topic_id": "t"})
            self.assertIn("topics", out2)
            self.assertEqual(1, stub_update.call_count)
            self.assertIs(stub_update.call_args[0][0], store)

    def test_update_index_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-heads-deleg-") as td:
            store = MemoryStore(td)
            bead = {"id": "bead-1", "title": "X"}
            with patch("core_memory.persistence.store_index_heads_ops.update_index_for_store", return_value=None) as stub:
                store._update_index(bead)
            self.assertEqual(1, stub.call_count)
            self.assertIs(stub.call_args[0][0], store)
            self.assertIs(stub.call_args[0][1], bead)


if __name__ == "__main__":
    unittest.main()
