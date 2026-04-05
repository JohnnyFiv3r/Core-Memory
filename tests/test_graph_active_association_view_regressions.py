from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.graph import sync_structural_pipeline
from core_memory.graph.api import causal_traverse as api_causal_traverse
from core_memory.graph.traversal import causal_traverse
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestGraphActiveAssociationViewRegressions(unittest.TestCase):
    @staticmethod
    def _retract_association(root: Path, assoc_id: str) -> None:
        idx_file = root / ".beads" / "index.json"
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
        for row in (idx.get("associations") or []):
            if str(row.get("id") or "") == str(assoc_id):
                row["status"] = "retracted"
        idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

    @staticmethod
    def _chains_have_edge(chains: list[dict], src: str, dst: str, rel: str | None = None) -> bool:
        for c in chains or []:
            for e in (c.get("edges") or []):
                if str(e.get("src") or "") != str(src):
                    continue
                if str(e.get("dst") or "") != str(dst):
                    continue
                if rel is not None and str(e.get("rel") or "") != str(rel):
                    continue
                return True
        return False

    def test_traversal_skips_retracted_association(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="outcome", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            assoc_id = s.link(source_id=a, target_id=b, relationship="supports", explanation="link")

            self._retract_association(Path(td), assoc_id)

            out = causal_traverse(Path(td), start_bead_ids=[a], direction="forward", max_depth=3)
            self.assertTrue(out.get("ok"))
            ids = {str(r.get("bead_id") or "") for r in (out.get("results") or [])}
            self.assertIn(a, ids)
            self.assertNotIn(b, ids)

    def test_api_causal_traverse_skips_retracted_association(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            assoc_id = s.link(source_id=a, target_id=b, relationship="supports", explanation="link")

            self._retract_association(Path(td), assoc_id)

            out = api_causal_traverse(Path(td), anchor_ids=[a], max_depth=3, max_chains=10)
            self.assertTrue(out.get("ok"))
            self.assertFalse(self._chains_have_edge(out.get("chains") or [], a, b, "supports"))
            diag = out.get("assoc_diag") or {}
            self.assertGreaterEqual(int(diag.get("assoc_edges_inactive_filtered") or 0), 1)

    def test_canonical_retrieval_surfaces_skip_retracted_association(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="alpha decision", summary=["alpha"], tags=["alpha"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="beta evidence", summary=["beta"], tags=["beta"], session_id="s1", source_turn_ids=["t2"])
            assoc_id = s.link(source_id=a, target_id=b, relationship="supports", explanation="link")

            self._retract_association(Path(td), assoc_id)

            search_out = memory_tools.search(
                request={
                    "query_text": "alpha decision",
                    "intent": "remember",
                    "k": 5,
                    "require_structural": True,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(search_out.get("ok"))
            self.assertFalse(self._chains_have_edge(search_out.get("chains") or [], a, b, "supports"))

            trace_out = memory_tools.trace(query="", anchor_ids=[a], root=td, k=5)
            self.assertTrue(trace_out.get("ok"))
            self.assertFalse(self._chains_have_edge(trace_out.get("chains") or [], a, b, "supports"))

            reason_out = memory_reason(query="why alpha decision", root=td, k=5, pinned_bead_ids=[a])
            self.assertTrue(reason_out.get("ok"))
            self.assertFalse(self._chains_have_edge(reason_out.get("chains") or [], a, b, "supports"))

    def test_structural_sync_removes_stale_association_sync_links(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="outcome", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            assoc_id = s.link(source_id=a, target_id=b, relationship="supports", explanation="link")

            app1 = sync_structural_pipeline(Path(td), apply=True, strict=False)
            self.assertTrue(app1.get("ok"))

            idx_file = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            bead_a = (idx.get("beads") or {}).get(a) or {}
            links = bead_a.get("links") or []
            self.assertTrue(any(isinstance(l, dict) and str(l.get("source") or "") == "association_sync" for l in links))

            # retract association and resync
            self._retract_association(Path(td), assoc_id)

            app2 = sync_structural_pipeline(Path(td), apply=True, strict=False)
            self.assertTrue(app2.get("ok"))

            idx2 = json.loads(idx_file.read_text(encoding="utf-8"))
            bead_a2 = (idx2.get("beads") or {}).get(a) or {}
            links2 = bead_a2.get("links") or []
            self.assertFalse(
                any(
                    isinstance(l, dict)
                    and str(l.get("source") or "") == "association_sync"
                    and str(l.get("bead_id") or "") == b
                    for l in links2
                )
            )


if __name__ == "__main__":
    unittest.main()
