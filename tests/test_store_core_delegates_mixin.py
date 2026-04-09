from __future__ import annotations

import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_core_delegates_mixin import StoreCoreDelegatesMixin


class TestStoreCoreDelegatesMixinSlice95A(unittest.TestCase):
    def test_memory_store_inherits_core_delegates_mixin(self):
        self.assertTrue(issubclass(MemoryStore, StoreCoreDelegatesMixin))

    def test_selected_core_methods_resolve_from_mixin(self):
        self.assertIs(StoreCoreDelegatesMixin.add_bead, MemoryStore.add_bead)
        self.assertIs(StoreCoreDelegatesMixin.query, MemoryStore.query)
        self.assertIs(StoreCoreDelegatesMixin.retrieve_with_context, MemoryStore.retrieve_with_context)
        self.assertIs(StoreCoreDelegatesMixin.compact, MemoryStore.compact)
        self.assertIs(StoreCoreDelegatesMixin.rebuild_index, MemoryStore.rebuild_index)


if __name__ == "__main__":
    unittest.main()
