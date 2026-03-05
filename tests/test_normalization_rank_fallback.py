import unittest

from core_memory.retrieval.hybrid import _normalize


class TestNormalizationRankFallback(unittest.TestCase):
    def test_rank_fallback_on_equal_scores(self):
        vals, mode = _normalize([1.0, 1.0, 1.0])
        self.assertEqual("rank", mode)
        self.assertEqual([1.0, 0.5, 0.0], vals)


if __name__ == "__main__":
    unittest.main()
