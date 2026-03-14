import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.engine import crawler_turn_context, apply_crawler_turn_updates
from core_memory.persistence.store import MemoryStore


class TestAssociationCrawlerContract(unittest.TestCase):
    def test_context_and_append_only_updates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            ctx = crawler_turn_context(root=td, session_id="s1", carry_in_bead_ids=[b2])
            self.assertEqual("crawler_turn_context", (ctx.get("engine") or {}).get("entry"))
            self.assertGreaterEqual(len(ctx.get("beads") or []), 2)
            self.assertIn(b2, ctx.get("visible_bead_ids") or [])

            out = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=ctx.get("visible_bead_ids") or [],
                updates={
                    "reviewed_beads": [
                        {
                            "bead_id": b1,
                            "promotion_state": "preserve_full_in_rolling",
                            "reason": "useful continuity",
                            "associations": [
                                {
                                    "target_bead_id": b2,
                                    "relationship": "supports",
                                    "confidence": 0.81,
                                    "rationale": "same session context",
                                }
                            ],
                        }
                    ]
                },
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("apply_crawler_turn_updates", (out.get("engine") or {}).get("entry"))
            self.assertEqual(1, out.get("promotions_marked"))
            self.assertEqual(1, out.get("associations_appended"))
            self.assertEqual("session_side_log", out.get("authority_path"))

            idx = s._read_json(s.beads_dir / "index.json")
            self.assertFalse((idx.get("beads", {}).get(b1) or {}).get("promotion_marked"))
            self.assertFalse(any(a.get("source_bead") == b1 and a.get("target_bead") == b2 for a in idx.get("associations", [])))

            log_path = Path(out.get("queued_to") or "")
            self.assertTrue(log_path.exists())
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(2, len(rows))
            self.assertTrue(any(r.get("kind") == "promotion_mark" and r.get("bead_id") == b1 for r in rows))
            self.assertTrue(any(r.get("kind") == "association_append" and r.get("source_bead") == b1 and r.get("target_bead") == b2 for r in rows))

    def test_association_target_must_be_visible(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s2", source_turn_ids=["t2"])

            out = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[b1],
                updates={
                    "associations": [
                        {"source_bead_id": b1, "target_bead_id": b2, "relationship": "supports"}
                    ]
                },
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual(0, out.get("associations_appended"))


if __name__ == "__main__":
    unittest.main()
