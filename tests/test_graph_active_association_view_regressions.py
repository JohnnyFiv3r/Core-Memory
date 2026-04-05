from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.graph import sync_structural_pipeline
from core_memory.graph.traversal import causal_traverse
from core_memory.persistence.store import MemoryStore


class TestGraphActiveAssociationViewRegressions(unittest.TestCase):
    def test_traversal_skips_retracted_association(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="outcome", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            assoc_id = s.link(source_id=a, target_id=b, relationship="supports", explanation="link")

            idx_file = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for row in (idx.get("associations") or []):
                if str(row.get("id") or "") == str(assoc_id):
                    row["status"] = "retracted"
            idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

            out = causal_traverse(Path(td), start_bead_ids=[a], direction="forward", max_depth=3)
            self.assertTrue(out.get("ok"))
            ids = {str(r.get("bead_id") or "") for r in (out.get("results") or [])}
            self.assertIn(a, ids)
            self.assertNotIn(b, ids)

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
            for row in (idx.get("associations") or []):
                if str(row.get("id") or "") == str(assoc_id):
                    row["status"] = "retracted"
            idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

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
