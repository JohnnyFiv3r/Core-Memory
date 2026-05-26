from __future__ import annotations

import unittest

from core_memory.persistence.store import MemoryStore


class TestStoreReportingPromotionContractSlice94A(unittest.TestCase):
    def test_reporting_promotion_methods_exist_on_memory_store(self):
        for method in ("metrics_report", "autonomy_report", "schema_quality_report",
                       "promotion_slate", "decide_promotion", "rebalance_promotions"):
            self.assertTrue(callable(getattr(MemoryStore, method, None)), f"MemoryStore.{method} missing")


if __name__ == "__main__":
    unittest.main()
