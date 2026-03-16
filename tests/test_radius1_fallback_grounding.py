import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestRadius1FallbackGrounding(unittest.TestCase):
    def test_fallback_uses_links_when_no_graph_chain(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Gate", summary=["candidate"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Metrics", summary=["inflation"], session_id="main", source_turn_ids=["t1"])
            idx = s._read_json(s.beads_dir / "index.json")
            idx["beads"][d].setdefault("links", []).append({"type": "supports", "bead_id": e})
            s._write_json(s.beads_dir / "index.json", idx)

            out = memory_reason("why gate", root=td)
            self.assertTrue(out.get("ok"))
            chains = out.get("chains") or []
            self.assertTrue(len(chains) >= 1)
            self.assertTrue(any(len(c.get("edges") or []) > 0 for c in chains))


if __name__ == "__main__":
    unittest.main()
