import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.engine import crawler_turn_context, apply_crawler_turn_updates
from core_memory.persistence.store import MemoryStore
from core_memory.association.crawler_contract import merge_crawler_updates


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

    def test_reviewed_nested_association_preserves_v21_fields(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])

            out = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[b1, b2],
                updates={
                    "reviewed_beads": [
                        {
                            "bead_id": b1,
                            "associations": [
                                {
                                    "target_bead_id": b2,
                                    "relationship": "supports",
                                    "reason_text": "evidence supports the statement",
                                    "confidence": 0.88,
                                    "provenance": "model_inferred",
                                    "reason_code": "supporting_evidence",
                                    "evidence_fields": ["summary"],
                                }
                            ],
                        }
                    ]
                },
            )

            self.assertTrue(out.get("ok"))
            self.assertEqual(1, out.get("associations_appended"))
            self.assertEqual(0, out.get("associations_quarantined"))

            log_path = Path(out.get("queued_to") or "")
            self.assertTrue(log_path.exists())
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            assoc_rows = [r for r in rows if r.get("kind") == "association_append"]
            self.assertEqual(1, len(assoc_rows))
            row = assoc_rows[0]
            self.assertEqual("evidence supports the statement", row.get("reason_text"))
            self.assertEqual(0.88, row.get("confidence"))
            self.assertEqual("model_inferred", row.get("provenance"))
            self.assertEqual("supporting_evidence", row.get("reason_code"))
            self.assertEqual(["summary"], row.get("evidence_fields"))

    def test_association_lifecycle_overlay_supersede_and_retract(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="context", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="context", title="B", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            b3 = s.add_bead(type="context", title="C", summary=["z"], session_id="s1", source_turn_ids=["t3"])

            out_a = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[b1, b2, b3],
                updates={
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "supports",
                            "reason_text": "A supports B",
                            "confidence": 0.8,
                        },
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b3,
                            "relationship": "supports",
                            "reason_text": "A supports C",
                            "confidence": 0.82,
                        },
                    ]
                },
            )
            self.assertTrue(out_a.get("ok"))
            merge_a = merge_crawler_updates(td, "s1")
            self.assertTrue(merge_a.get("ok"))
            self.assertEqual(2, int(merge_a.get("associations_appended") or 0))

            idx = s._read_json(s.beads_dir / "index.json")
            assocs = [a for a in (idx.get("associations") or []) if a.get("source_bead") == b1]
            self.assertGreaterEqual(len(assocs), 2)
            old_id = str(assocs[0].get("id") or "")
            new_id = str(assocs[1].get("id") or "")

            out_b = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[b1, b2, b3],
                updates={
                    "association_lifecycle": [
                        {
                            "association_id": old_id,
                            "action": "supersede",
                            "replacement_association_id": new_id,
                            "reason_text": "better edge",
                        },
                        {
                            "association_id": new_id,
                            "action": "retract",
                            "reason_text": "invalidated",
                        },
                    ]
                },
            )
            self.assertTrue(out_b.get("ok"))
            self.assertEqual(2, int(out_b.get("association_lifecycle_queued") or 0))

            merge_b = merge_crawler_updates(td, "s1")
            self.assertTrue(merge_b.get("ok"))
            self.assertEqual(2, int(merge_b.get("association_lifecycle_applied") or 0))

            idx2 = s._read_json(s.beads_dir / "index.json")
            by_id = {str(a.get("id") or ""): a for a in (idx2.get("associations") or [])}
            self.assertEqual("superseded", str((by_id.get(old_id) or {}).get("status") or ""))
            self.assertEqual(new_id, str((by_id.get(old_id) or {}).get("superseded_by_association_id") or ""))
            self.assertEqual("retracted", str((by_id.get(new_id) or {}).get("status") or ""))

    def test_lifecycle_scope_rejects_cross_session_target(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            # s1 pair
            a1 = s.add_bead(type="context", title="A1", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b1 = s.add_bead(type="context", title="B1", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            # s2 pair
            a2 = s.add_bead(type="context", title="A2", summary=["x"], session_id="s2", source_turn_ids=["u1"])
            b2 = s.add_bead(type="context", title="B2", summary=["y"], session_id="s2", source_turn_ids=["u2"])

            # create associations in both sessions
            out1 = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[a1, b1],
                updates={
                    "associations": [
                        {
                            "source_bead_id": a1,
                            "target_bead_id": b1,
                            "relationship": "supports",
                            "reason_text": "s1",
                            "confidence": 0.8,
                        }
                    ]
                },
            )
            self.assertTrue(out1.get("ok"))
            merge_crawler_updates(td, "s1")

            out2 = apply_crawler_turn_updates(
                root=td,
                session_id="s2",
                visible_bead_ids=[a2, b2],
                updates={
                    "associations": [
                        {
                            "source_bead_id": a2,
                            "target_bead_id": b2,
                            "relationship": "supports",
                            "reason_text": "s2",
                            "confidence": 0.8,
                        }
                    ]
                },
            )
            self.assertTrue(out2.get("ok"))
            merge_crawler_updates(td, "s2")

            idx = s._read_json(s.beads_dir / "index.json")
            assocs = [a for a in (idx.get("associations") or []) if a.get("relationship") == "supports"]
            self.assertGreaterEqual(len(assocs), 2)

            s2_assoc = None
            for a in assocs:
                if str(a.get("source_bead") or "") == a2 and str(a.get("target_bead") or "") == b2:
                    s2_assoc = a
                    break
            self.assertIsNotNone(s2_assoc)
            s2_assoc_id = str((s2_assoc or {}).get("id") or "")

            # attempt to mutate s2 assoc from s1 context
            out_bad = apply_crawler_turn_updates(
                root=td,
                session_id="s1",
                visible_bead_ids=[a1, b1],
                updates={
                    "association_lifecycle": [
                        {
                            "association_id": s2_assoc_id,
                            "action": "retract",
                            "reason_text": "cross-session should be rejected",
                        }
                    ]
                },
            )
            self.assertTrue(out_bad.get("ok"))
            self.assertEqual(1, int(out_bad.get("association_lifecycle_rejected") or 0))
            self.assertEqual(0, int(out_bad.get("association_lifecycle_queued") or 0))

            merge_bad = merge_crawler_updates(td, "s1")
            self.assertTrue(merge_bad.get("ok"))
            self.assertEqual(0, int(merge_bad.get("association_lifecycle_applied") or 0))

            idx2 = s._read_json(s.beads_dir / "index.json")
            by_id = {str(a.get("id") or ""): a for a in (idx2.get("associations") or [])}
            self.assertEqual("active", str((by_id.get(s2_assoc_id) or {}).get("status") or "active"))


if __name__ == "__main__":
    unittest.main()
