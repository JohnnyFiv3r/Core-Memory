import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.lexical import lexical_lookup
from core_memory.store import MemoryStore


class TestLexicalRecallSmoke(unittest.TestCase):
    def test_exact_phrase_hits(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            target = s.add_bead(type="evidence", title="Promotion inflation 92 percent", summary=["compaction starvation"], session_id="main", source_turn_ids=["t1"])
            s.add_bead(type="context", title="Other", summary=["unrelated"], session_id="main", source_turn_ids=["t2"])
            out = lexical_lookup(Path(td), "92 percent promotion inflation", k=3)
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            self.assertIn(target, ids)


if __name__ == "__main__":
    unittest.main()
