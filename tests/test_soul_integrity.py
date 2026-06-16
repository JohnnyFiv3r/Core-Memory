import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.soul.integrity import soul_integrity_check, soul_integrity_repair
from core_memory.soul.store import propose_soul_update, read_soul_file, current_soul_entries


def _upsert(root, file, key, content, **kw):
    return propose_soul_update(root, target_file=file, entry_key=key, content=content,
                               requires_approval=False, **kw)


class TestSoulIntegrityCheck(unittest.TestCase):
    def test_clean_store_has_no_issues(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "SOUL.md", "Summary", "We value continuity.")
            out = soul_integrity_check(td)
            self.assertTrue(out["ok"])
            self.assertEqual(0, out["issue_count"])

    def test_empty_entry_flagged_repairable(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "g1", "   ")
            out = soul_integrity_check(td)
            codes = [i["code"] for i in out["issues"]]
            self.assertIn("empty_entry", codes)
            self.assertEqual(1, out["repairable_count"])
            empty = next(i for i in out["issues"] if i["code"] == "empty_entry")
            self.assertTrue(empty["repairable"])
            self.assertEqual("GOALS.md", empty["target_file"])

    def test_duplicate_content_flagged_not_repairable(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "TENSIONS.md", "t1", "Speed versus accuracy.")
            _upsert(td, "TENSIONS.md", "t2", "speed   VERSUS accuracy.")  # normalizes equal
            out = soul_integrity_check(td)
            dups = [i for i in out["issues"] if i["code"] == "duplicate_content"]
            self.assertEqual(1, len(dups))
            self.assertFalse(dups[0]["repairable"])

    def test_broken_evidence_reference_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "IDENTITY.md", "v1", "Values simplicity.",
                    evidence=[{"bead_id": "bead-does-not-exist", "relationship": "supports"}])
            out = soul_integrity_check(td)
            broken = [i for i in out["issues"] if i["code"] == "broken_evidence_reference"]
            self.assertEqual(1, len(broken))
            self.assertIn("bead-does-not-exist", broken[0]["missing_bead_ids"])
            self.assertFalse(broken[0]["repairable"])

    def test_subject_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "g1", "  ", subject="acme")
            self.assertEqual(0, soul_integrity_check(td, subject="self")["issue_count"])
            self.assertEqual(1, soul_integrity_check(td, subject="acme")["issue_count"])


class TestSoulIntegrityRepair(unittest.TestCase):
    def test_repair_removes_empty_entry(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "real", "Reduce onboarding friction.")
            _upsert(td, "GOALS.md", "blank", "")
            out = soul_integrity_repair(td)
            self.assertTrue(out["ok"])
            self.assertEqual(1, out["repaired_count"])
            entries = current_soul_entries(td, file_name="GOALS.md")["entries"]
            self.assertIn("real", entries)
            self.assertNotIn("blank", entries)
            # Idempotent: re-running finds nothing to repair.
            self.assertEqual(0, soul_integrity_repair(td)["repaired_count"])

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "blank", "  ")
            out = soul_integrity_repair(td, apply=False)
            self.assertFalse(out["applied"])
            self.assertEqual(0, out["repaired_count"])
            self.assertEqual(1, len(out["would_repair"]))
            # Still present — nothing was written.
            self.assertIn("blank", current_soul_entries(td, file_name="GOALS.md")["entries"])

    def test_repair_leaves_non_repairable_issues(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "TENSIONS.md", "t1", "Speed versus accuracy.")
            _upsert(td, "TENSIONS.md", "t2", "Speed versus accuracy.")
            out = soul_integrity_repair(td)
            self.assertEqual(0, out["repaired_count"])
            self.assertTrue(any(i["code"] == "duplicate_content" for i in out["skipped"]))
            # Both entries remain — meaning decisions are not auto-made.
            self.assertEqual(2, len(current_soul_entries(td, file_name="TENSIONS.md")["entries"]))


if __name__ == "__main__":
    unittest.main()
