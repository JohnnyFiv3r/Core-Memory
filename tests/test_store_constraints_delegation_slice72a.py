from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreConstraintsDelegationSlice72A(unittest.TestCase):
    def test_active_constraints_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-constraints-") as td:
            store = MemoryStore(td)
            expected = [{"bead_id": "b1", "constraints": ["must canary"]}]
            with patch("core_memory.persistence.store_constraints.active_constraints_for_store", return_value=expected) as stub:
                out = store.active_constraints(limit=5)
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual(5, kwargs.get("limit"))

    def test_check_plan_constraints_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-constraints-") as td:
            store = MemoryStore(td)
            expected = {"ok": True, "recommendation": "proceed"}
            with patch("core_memory.persistence.store_constraints.check_plan_constraints_for_store", return_value=expected) as stub:
                out = store.check_plan_constraints(plan="use canary", limit=7)
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("use canary", kwargs.get("plan"))
            self.assertEqual(7, kwargs.get("limit"))


if __name__ == "__main__":
    unittest.main()
