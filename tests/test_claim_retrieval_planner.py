import unittest

from core_memory.claim.retrieval_planner import RETRIEVAL_MODES, boost_claim_results, plan_retrieval_mode


class TestClaimRetrievalPlanner(unittest.TestCase):
    def test_empty_query_returns_mixed(self):
        self.assertEqual("mixed", plan_retrieval_mode("", None, None))

    def test_causal_query(self):
        self.assertEqual("causal_first", plan_retrieval_mode("why did this happen?", None, None))

    def test_temporal_query(self):
        self.assertEqual("temporal_first", plan_retrieval_mode("what happened recently?", None, None))

    def test_fact_query(self):
        self.assertEqual("fact_first", plan_retrieval_mode("what is the capital of France?", None, None))

    def test_known_subject_boosts_fact_first(self):
        current_state = {"slots": {"user:preference": {"status": "active", "current_claim": {"id": "c1"}}}}
        self.assertEqual("fact_first", plan_retrieval_mode("what is my preference?", None, current_state))

    def test_mixed_default(self):
        self.assertEqual("mixed", plan_retrieval_mode("tell me something interesting", None, None))

    def test_boost_empty_state(self):
        results = [{"score": 0.9, "id": "b1"}]
        self.assertEqual(results, boost_claim_results(results, None))

    def test_boost_preserves_results(self):
        results = [{"score": 0.9, "id": "b1"}, {"score": 0.7, "id": "b2"}]
        boosted = boost_claim_results(results, {"slots": {}})
        self.assertEqual(2, len(boosted))

    def test_all_retrieval_modes_defined(self):
        for mode in ["fact_first", "causal_first", "temporal_first", "mixed"]:
            self.assertIn(mode, RETRIEVAL_MODES)


if __name__ == "__main__":
    unittest.main()
