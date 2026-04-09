from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreDreamBootstrapOpsDelegationSlice86A(unittest.TestCase):
    def test_init_index_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-bootstrap-deleg-") as td:
            with patch("core_memory.persistence.store_dream_bootstrap_ops.init_index_for_store", return_value=None) as stub:
                _store = MemoryStore(td)
            self.assertGreaterEqual(stub.call_count, 1)

    def test_dream_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-bootstrap-deleg-") as td:
            store = MemoryStore(td)
            expected = [{"relationship": "supports"}]
            with patch("core_memory.persistence.store_dream_bootstrap_ops.dream_for_store", return_value=expected) as stub:
                out = store.dream(novel_only=True, seen_window_runs=3, max_exposure=2)
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertTrue(kwargs.get("novel_only"))
            self.assertEqual(3, kwargs.get("seen_window_runs"))
            self.assertEqual(2, kwargs.get("max_exposure"))


if __name__ == "__main__":
    unittest.main()
