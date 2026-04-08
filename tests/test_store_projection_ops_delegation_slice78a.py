from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreProjectionOpsDelegationSlice78A(unittest.TestCase):
    def test_rebuild_index_projection_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-proj-deleg-") as td:
            store = MemoryStore(td)
            expected = {
                "ok": True,
                "mode": "session_first_projection_cache",
                "total_beads": 1,
                "total_associations": 0,
            }
            with patch(
                "core_memory.persistence.store_projection_ops.rebuild_index_projection_from_sessions_for_store",
                return_value=expected,
            ) as stub:
                out = store.rebuild_index_projection_from_sessions()

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, _kwargs = stub.call_args
            self.assertIs(args[0], store)


if __name__ == "__main__":
    unittest.main()
