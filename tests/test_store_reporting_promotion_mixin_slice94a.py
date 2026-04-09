from __future__ import annotations

import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_reporting_promotion_mixin import StoreReportingPromotionMixin


class TestStoreReportingPromotionMixinSlice94A(unittest.TestCase):
    def test_memory_store_inherits_reporting_promotion_mixin(self):
        self.assertTrue(issubclass(MemoryStore, StoreReportingPromotionMixin))

    def test_selected_methods_resolve_from_mixin(self):
        # These methods should be inherited from the mixin rather than redefined in store.py
        self.assertIs(StoreReportingPromotionMixin.metrics_report, MemoryStore.metrics_report)
        self.assertIs(StoreReportingPromotionMixin.autonomy_report, MemoryStore.autonomy_report)
        self.assertIs(StoreReportingPromotionMixin.schema_quality_report, MemoryStore.schema_quality_report)
        self.assertIs(StoreReportingPromotionMixin.promotion_slate, MemoryStore.promotion_slate)
        self.assertIs(StoreReportingPromotionMixin.decide_promotion, MemoryStore.decide_promotion)


if __name__ == "__main__":
    unittest.main()
