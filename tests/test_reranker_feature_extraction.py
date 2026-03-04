import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.store import MemoryStore


class TestRerankerFeatureExtraction(unittest.TestCase):
    def test_features_present(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Decision one", summary=["promotion"], session_id="main", source_turn_ids=["t1"])
            h = hybrid_lookup(Path(td), "promotion", k=3)
            rr = rerank_candidates(Path(td), "promotion", h.get("results") or [])
            self.assertTrue(rr.get("ok"))
            r0 = (rr.get("results") or [])[0]
            f = r0.get("features") or {}
            for key in [
                "has_decision",
                "has_evidence",
                "has_outcome",
                "has_structural_edges",
                "query_term_coverage",
                "penalty_low_info_title",
                "penalty_orphan",
                "penalty_superseded_only",
            ]:
                self.assertIn(key, f)


if __name__ == "__main__":
    unittest.main()
