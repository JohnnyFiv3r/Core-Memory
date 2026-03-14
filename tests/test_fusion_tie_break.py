import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.persistence.store import MemoryStore


class TestFusionTieBreak(unittest.TestCase):
    def test_order_is_deterministic_and_policy_exposed(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="Same", summary=["token"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="Same", summary=["token"], session_id="main", source_turn_ids=["t2"])
            a = hybrid_lookup(Path(td), "token", k=5)
            b = hybrid_lookup(Path(td), "token", k=5)
            ids_a = [r.get("bead_id") for r in (a.get("results") or [])]
            ids_b = [r.get("bead_id") for r in (b.get("results") or [])]
            self.assertEqual(ids_a, ids_b)
            self.assertEqual((a.get("results") or [])[0].get("tie_break_policy"), "fused>sem>lex>bead_id")


if __name__ == "__main__":
    unittest.main()
