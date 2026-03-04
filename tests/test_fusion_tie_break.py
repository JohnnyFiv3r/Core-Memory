import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.store import MemoryStore


class TestFusionTieBreak(unittest.TestCase):
    def test_tie_break_uses_bead_id(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="Same", summary=["token"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="Same", summary=["token"], session_id="main", source_turn_ids=["t2"])
            out = hybrid_lookup(Path(td), "token", k=5)
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            self.assertEqual(sorted(ids[:2]), sorted([a, b]))


if __name__ == "__main__":
    unittest.main()
