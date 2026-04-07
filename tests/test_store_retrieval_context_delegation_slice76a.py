from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreRetrievalContextDelegationSlice76A(unittest.TestCase):
    def test_retrieve_with_context_delegates(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-retrieval-deleg-") as td:
            store = MemoryStore(td)
            expected = {"ok": True, "mode": "strict", "results": []}
            with patch(
                "core_memory.persistence.store_retrieval_context.retrieve_with_context_for_store",
                return_value=expected,
            ) as stub:
                out = store.retrieve_with_context(
                    query_text="what happened",
                    context_tags=["project"],
                    limit=7,
                    strict_first=True,
                    deep_recall=True,
                    max_uncompact_per_turn=3,
                    auto_memory_intent=False,
                )

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("what happened", kwargs.get("query_text"))
            self.assertEqual(["project"], kwargs.get("context_tags"))
            self.assertEqual(7, kwargs.get("limit"))
            self.assertTrue(kwargs.get("strict_first"))
            self.assertTrue(kwargs.get("deep_recall"))
            self.assertEqual(3, kwargs.get("max_uncompact_per_turn"))
            self.assertFalse(kwargs.get("auto_memory_intent"))


if __name__ == "__main__":
    unittest.main()
