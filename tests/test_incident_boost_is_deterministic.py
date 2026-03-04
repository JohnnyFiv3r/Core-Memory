import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.incidents import tag_incident
from core_memory.store import MemoryStore


class TestIncidentBoostDeterministic(unittest.TestCase):
    def test_boost_and_order_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="promotion note", summary=["promotion inflation"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="other", summary=["promotion inflation"], session_id="main", source_turn_ids=["t2"])
            tag_incident(Path(td), "promotion_inflation_2026q1", [a])
            r1 = hybrid_lookup(Path(td), "promotion inflation", k=5)
            r2 = hybrid_lookup(Path(td), "promotion inflation", k=5)
            ids1 = [x.get("bead_id") for x in (r1.get("results") or [])]
            ids2 = [x.get("bead_id") for x in (r2.get("results") or [])]
            self.assertEqual(ids1, ids2)
            self.assertEqual(a, ids1[0])


if __name__ == "__main__":
    unittest.main()
