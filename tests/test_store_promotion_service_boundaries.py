import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStorePromotionServiceBoundariesSlice46A2(unittest.TestCase):
    def test_promotion_slate_delegates_to_service(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            expected = {"ok": True, "candidate_total": 0, "results": []}
            with patch("core_memory.persistence.store_promotion_ops.promotion_slate_entry_for_store", return_value=expected) as spy:
                out = s.promotion_slate(limit=5, query_text="policy")
            self.assertEqual(expected, out)
            spy.assert_called_once_with(s, limit=5, query_text="policy")

    def test_decide_promotion_delegates_to_service(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            expected = {"ok": True, "decision": "promote"}
            with patch("core_memory.persistence.store_promotion_ops.decide_promotion_entry_for_store", return_value=expected) as spy:
                out = s.decide_promotion(bead_id="b1", decision="promote", reason="x")
            self.assertEqual(expected, out)
            spy.assert_called_once()

    def test_rebalance_delegates_to_service(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            expected = {"ok": True, "applied": 0}
            with patch("core_memory.persistence.store_promotion_ops.rebalance_promotions_entry_for_store", return_value=expected) as spy:
                out = s.rebalance_promotions(apply=True)
            self.assertEqual(expected, out)
            spy.assert_called_once_with(s, apply=True)


if __name__ == "__main__":
    unittest.main()
