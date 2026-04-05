from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.association.health import association_health_report
from core_memory.persistence.store import MemoryStore


class TestAssociationHealthSlice5(unittest.TestCase):
    def test_health_report_counts_and_noise_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            c = s.add_bead(type="context", title="C", summary=["z"], session_id="s2", source_turn_ids=["t3"])
            s.link(source_id=a, target_id=b, relationship="follows", explanation="temporal")
            sid = s.link(source_id=a, target_id=c, relationship="supports", explanation="semantic")

            # mark one association inactive
            idx_file = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            for row in (idx.get("associations") or []):
                if str(row.get("id") or "") == str(sid):
                    row["status"] = "retracted"
            idx_file.write_text(json.dumps(idx, indent=2), encoding="utf-8")

            out = association_health_report(td)
            self.assertTrue(out.get("ok"))
            self.assertEqual(3, int(out.get("beads") or 0))
            self.assertEqual(2, int(out.get("associations_total") or 0))
            self.assertEqual(1, int(out.get("associations_active") or 0))
            self.assertIn("retracted", dict(out.get("status_distribution") or {}))

    def test_health_report_session_scope(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["y"], session_id="s2", source_turn_ids=["t2"])
            s.link(source_id=a, target_id=b, relationship="supports", explanation="cross")

            out = association_health_report(td, session_id="s1")
            self.assertTrue(out.get("ok"))
            self.assertEqual("s1", out.get("session_id"))
            self.assertEqual(1, int(out.get("beads") or 0))


if __name__ == "__main__":
    unittest.main()
