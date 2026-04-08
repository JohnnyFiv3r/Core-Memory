from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreFailureOpsDelegationSlice87A(unittest.TestCase):
    def test_compute_and_preflight_delegate(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-failure-deleg-") as td:
            store = MemoryStore(td)

            with patch("core_memory.persistence.store_failure_ops.compute_failure_signature_for_store", return_value="sig-1") as stub_sig:
                sig = store.compute_failure_signature("bad plan")
            self.assertEqual("sig-1", sig)
            self.assertEqual(1, stub_sig.call_count)
            self.assertIs(stub_sig.call_args[0][0], store)
            self.assertEqual("bad plan", stub_sig.call_args[0][1])

            expected = {"ok": True, "risk": "low"}
            with patch("core_memory.persistence.store_failure_ops.preflight_failure_check_for_store", return_value=expected) as stub_pre:
                out = store.preflight_failure_check("plan", limit=3, context_tags=["release"])
            self.assertEqual(expected, out)
            self.assertEqual(1, stub_pre.call_count)
            args, kwargs = stub_pre.call_args
            self.assertIs(args[0], store)
            self.assertEqual("plan", kwargs.get("plan"))
            self.assertEqual(3, kwargs.get("limit"))
            self.assertEqual(["release"], kwargs.get("context_tags"))

    def test_find_matches_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-failure-deleg-") as td:
            store = MemoryStore(td)
            expected = [{"id": "bead-1", "tag_overlap": 2}]
            with patch(
                "core_memory.persistence.store_failure_ops.find_failure_signature_matches_for_store",
                return_value=expected,
            ) as stub:
                out = store.find_failure_signature_matches(plan="", tags=["alpha"], limit=2)
            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("", kwargs.get("plan"))
            self.assertEqual(["alpha"], kwargs.get("tags"))
            self.assertEqual(2, kwargs.get("limit"))


if __name__ == "__main__":
    unittest.main()
