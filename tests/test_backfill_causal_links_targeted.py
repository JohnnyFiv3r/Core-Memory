import tempfile
import unittest
from pathlib import Path

from core_memory.graph import backfill_causal_links
from core_memory.persistence.store import MemoryStore


class TestBackfillCausalLinksTargeted(unittest.TestCase):
    def test_targeted_limits_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d1 = s.add_bead(type="decision", title="A decision", summary=["alpha"], session_id="main", source_turn_ids=["t1"])
            d2 = s.add_bead(type="decision", title="B decision", summary=["beta"], session_id="main", source_turn_ids=["t1"])
            e1 = s.add_bead(type="evidence", title="A evidence", summary=["alpha"], session_id="main", source_turn_ids=["t1"])
            e2 = s.add_bead(type="evidence", title="B evidence", summary=["beta"], session_id="main", source_turn_ids=["t1"])

            all_out = backfill_causal_links(Path(td), apply=False, min_overlap=1, include_bead_ids=[])
            tgt_out = backfill_causal_links(Path(td), apply=False, min_overlap=1, include_bead_ids=[d1])
            self.assertGreaterEqual(int(all_out.get("proposed", 0)), int(tgt_out.get("proposed", 0)))
            for row in tgt_out.get("sample") or []:
                self.assertTrue(d1 in {row.get("src_id"), row.get("dst_id")})


if __name__ == "__main__":
    unittest.main()
