import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.persistence.store import MemoryStore


class TestIntentWeightedRerank(unittest.TestCase):
    def test_weights_change_by_intent_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="candidate first promotion", summary=["promotion policy"], session_id="main", source_turn_ids=["t1"])
            h = hybrid_lookup(Path(td), "candidate first promotion", k=3)
            base = h.get("results") or []
            rr_causal = rerank_candidates(Path(td), "why candidate first promotion", base, intent_class="causal")
            rr_rem = rerank_candidates(Path(td), "remember candidate first promotion", base, intent_class="remember")
            d1 = ((rr_causal.get("results") or [])[0].get("derived") or {}).get("weights") or {}
            d2 = ((rr_rem.get("results") or [])[0].get("derived") or {}).get("weights") or {}
            self.assertNotEqual(d1.get("W_STRUCTURAL"), d2.get("W_STRUCTURAL"))
            self.assertNotEqual(d1.get("W_COVERAGE"), d2.get("W_COVERAGE"))


if __name__ == "__main__":
    unittest.main()
