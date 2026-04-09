from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreLifecycleOpsDelegationSlice92A(unittest.TestCase):
    def test_close_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-life-deleg-") as td:
            s = MemoryStore(td)
            with patch("core_memory.persistence.store_lifecycle_ops.close_store_for_store", return_value=None) as spy:
                s.close()
            self.assertEqual(1, spy.call_count)
            self.assertIs(spy.call_args[0][0], s)

    def test_del_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-life-deleg-") as td:
            s = MemoryStore(td)
            with patch("core_memory.persistence.store_lifecycle_ops.safe_del_for_store", return_value=None) as spy:
                s.__del__()
            self.assertEqual(1, spy.call_count)
            self.assertIs(spy.call_args[0][0], s)


if __name__ == "__main__":
    unittest.main()
