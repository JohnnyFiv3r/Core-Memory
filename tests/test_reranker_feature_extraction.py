import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.persistence.store import MemoryStore


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
                "chain_has_decision",
                "chain_has_evidence",
                "chain_has_outcome",
                "has_grounding_structural_edge",
                "structural_edge_count_clipped",
                "query_term_coverage",
                "low_info_score",
                "is_superseded",
                "has_active_chain_support",
                "incident_match_strength",
            ]:
                self.assertIn(key, f)


if __name__ == "__main__":
    unittest.main()
