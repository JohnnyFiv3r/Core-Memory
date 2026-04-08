import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore


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
            self.assertIn(bead2.get("status"), {"default", "candidate", "archived"})
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
            # candidate remains candidate without reinforcement (Phase B rule)
            self.assertEqual("candidate", idx["beads"][bid_candidate]["status"])

    def test_compact_promote_honors_canonical_candidate_promotion_state(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.auto_promote_on_compact = True
            bid_decision = s.add_bead(
                type="decision",
                title="Adopt integration port",
                summary=["stabilize adapters"],
                because=["single stable emit path"],
                detail="Detailed rationale that meets minimum payload.",
                session_id="main",
                source_turn_ids=["t2"],
            )
            bid_evidence = s.add_bead(
                type="evidence",
                title="Adapter regression data",
                summary=["supports decision"],
                detail="Observed failures in legacy fanout path.",
                session_id="main",
                source_turn_ids=["t2"],
            )
            s.link(bid_evidence, bid_decision, "supports", explanation="same-turn support", confidence=0.95)

            dec = s.decide_promotion(bead_id=bid_decision, decision="candidate", reason="review")
            self.assertTrue(dec.get("ok"))

            idx_pre = s._read_json(s.beads_dir / "index.json")
            row_pre = idx_pre["beads"][bid_decision]
            self.assertEqual("default", row_pre.get("status"))
            self.assertEqual("candidate", row_pre.get("promotion_state"))

            allow, _meta = s._candidate_promotable(idx_pre, row_pre)
            self.assertTrue(allow)

            out = s.compact(session_id="main", promote=True)
            self.assertTrue(out.get("ok"))

            idx_post = s._read_json(s.beads_dir / "index.json")
            row_post = idx_post["beads"][bid_decision]
            self.assertEqual("default", row_post.get("status"))
            self.assertEqual("promoted", row_post.get("promotion_state"))


if __name__ == "__main__":
    unittest.main()
