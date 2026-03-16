import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.persistence.store import MemoryStore


class TestRerankerScoringBounded(unittest.TestCase):
    def test_scores_in_0_1(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            for i in range(5):
                s.add_bead(type="context", title=f"item {i}", summary=["promotion inflation"], session_id="main", source_turn_ids=[f"t{i}"])
            h = hybrid_lookup(Path(td), "promotion inflation", k=5)
            rr = rerank_candidates(Path(td), "promotion inflation", h.get("results") or [])
            for r in rr.get("results") or []:
                sc = float(r.get("rerank_score") or 0.0)
                self.assertGreaterEqual(sc, 0.0)
                self.assertLessEqual(sc, 1.0)


if __name__ == "__main__":
    unittest.main()
