import json
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore


class TestCompactionPhaseA(unittest.TestCase):
    def test_compaction_writes_archive_snapshot_and_uncompact_restores(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bead_id = s.add_bead(
                type="decision",
                title="Switch to integration port",
                summary=["Unified emit path"],
                because=["cross orchestrator"],
                detail="Full detailed rationale and implementation notes for this decision.",
                session_id="main",
                source_turn_ids=["t1"],
            )

            c = s.compact(session_id="main", promote=False)
            self.assertTrue(c["ok"])
            self.assertEqual(1, c["compacted"])

            idx = s._read_json(s.beads_dir / "index.json")
            bead = idx["beads"][bead_id]
            self.assertEqual("archived", bead["status"])
            self.assertTrue((bead.get("archive_ptr") or {}).get("revision_id"))

            archive_file = Path(td) / ".beads" / "archive.jsonl"
            self.assertTrue(archive_file.exists())
            rows = [json.loads(l) for l in archive_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertTrue(any(r.get("bead_id") == bead_id and r.get("snapshot") for r in rows))

            u = s.uncompact(bead_id)
            self.assertTrue(u["ok"])
            idx2 = s._read_json(s.beads_dir / "index.json")
            bead2 = idx2["beads"][bead_id]
            self.assertIn(bead2.get("status"), {"open", "candidate", "archived"})
            self.assertIn("Full detailed rationale", bead2.get("detail", ""))

    def test_promote_in_compact_is_candidate_only_not_blanket(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            bid_context = s.add_bead(
                type="context",
                title="Routine response",
                summary=["ok"],
                detail="short",
                session_id="main",
                source_turn_ids=["t1"],
            )
            bid_candidate = s.add_bead(
                type="decision",
                title="Adopt integration port",
                summary=["stabilize adapters"],
                because=["single stable emit path"],
                detail="Detailed rationale that meets minimum payload.",
                status="candidate",
                session_id="main",
                source_turn_ids=["t2"],
            )

            s.compact(session_id="main", promote=True)
            idx = s._read_json(s.beads_dir / "index.json")
            # context should never be blanket-promoted
            self.assertNotEqual("promoted", idx["beads"][bid_context]["status"])
            # candidate decision can be promoted under gate
            self.assertEqual("promoted", idx["beads"][bid_candidate]["status"])


if __name__ == "__main__":
    unittest.main()
