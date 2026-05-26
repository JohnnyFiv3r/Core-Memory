from __future__ import annotations

import unittest

import pytest

pytestmark = pytest.mark.mixin_assembly

from core_memory.persistence.store import MemoryStore


class TestStoreCoreDelegatesContractSlice95A(unittest.TestCase):
    def test_core_methods_exist_on_memory_store(self):
        for method in ("add_bead", "query", "retrieve_with_context", "compact", "rebuild_index"):
            self.assertTrue(callable(getattr(MemoryStore, method, None)), f"MemoryStore.{method} missing")

    def test_internal_helpers_exist_on_memory_store(self):
        for method in ("_tokenize", "_read_json", "_write_json", "_read_heads", "_write_heads"):
            self.assertTrue(callable(getattr(MemoryStore, method, None)), f"MemoryStore.{method} missing")


if __name__ == "__main__":
    unittest.main()
