import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_semantic_edge, build_graph
from core_memory.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestStructuralEnrichmentAfterWeakChains(unittest.TestCase):
    def test_enriches_with_structural_fallback_when_only_weak_edges(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Gate decision", summary=["candidate only"], session_id="main", source_turn_ids=["t1"])
            c = s.add_bead(type="context", title="Related context", summary=["policy change"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Metrics", summary=["inflation"], session_id="main", source_turn_ids=["t1"])

            # weak/semantic edge path
            add_semantic_edge(Path(td), src_id=d, dst_id=c, rel="related_to", w=0.8)
            build_graph(Path(td), write_snapshot=True)

            # radius-1 structural fallback source via links
            idx = s._read_json(s.beads_dir / "index.json")
            idx["beads"][d].setdefault("links", []).append({"type": "supports", "bead_id": e})
            s._write_json(s.beads_dir / "index.json", idx)

            out = memory_reason("why gate decision", root=td)
            self.assertTrue(out.get("ok"))
            chains = out.get("chains") or []
            self.assertTrue(len(chains) >= 1)
            self.assertTrue(any(any(str(ed.get("rel") or "") == "supports" for ed in (ch.get("edges") or [])) for ch in chains))


if __name__ == "__main__":
    unittest.main()
