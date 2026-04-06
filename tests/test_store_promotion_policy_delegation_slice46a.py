import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStorePromotionPolicyDelegationSlice46A(unittest.TestCase):
    def test_promotion_score_delegates_to_policy_module(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with patch("core_memory.persistence.store.compute_promotion_score", return_value=(0.5, {"f": 1})) as spy:
                out = s._promotion_score({"beads": {}}, {"id": "b1"})
            self.assertEqual((0.5, {"f": 1}), out)
            spy.assert_called_once()

    def test_candidate_recommendation_rows_delegates_with_tokenizers(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            expected = ([{"bead_id": "b1", "recommendation": "review"}], 0.72)
            with patch("core_memory.persistence.store.get_recommendation_rows", return_value=expected) as spy:
                out = s._candidate_recommendation_rows({"beads": {}}, query_text="hello")
            self.assertEqual(expected, out)
            spy.assert_called_once()
            kwargs = spy.call_args.kwargs
            self.assertIn("query_tokenize_fn", kwargs)
            self.assertIn("query_expand_fn", kwargs)


if __name__ == "__main__":
    unittest.main()
