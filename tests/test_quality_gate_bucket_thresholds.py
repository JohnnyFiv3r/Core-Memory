import unittest

from core_memory.retrieval.quality_gate import quality_gate_decision


class TestQualityGateBuckets(unittest.TestCase):
    def test_short_vs_long_thresholds(self):
        base = [{"rerank_score": 0.34, "derived": {"structural_quality": 0.5}}]
        short = quality_gate_decision(base, query="why now")
        longq = quality_gate_decision(base, query="why did we decide this policy change")
        self.assertFalse(short.get("retry"))
        self.assertTrue(longq.get("retry"))


if __name__ == "__main__":
    unittest.main()
