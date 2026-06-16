import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.soul.integrity import soul_integrity_check, soul_integrity_repair
from core_memory.soul.store import (
    current_soul_entries,
    propose_soul_update,
    remove_entry_if_unchanged,
)


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

    def test_machine_empty_entry_is_repairable(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "IDENTITY.md", "value:simplicity", "   ", source="dreamer")
            out = soul_integrity_check(td)
            empty = next(i for i in out["issues"] if i["code"] == "empty_entry")
            self.assertTrue(empty["repairable"])
            self.assertEqual("dreamer", empty["source"])
            self.assertEqual(1, out["repairable_count"])

    def test_human_empty_entry_is_review_only(self):
        # A blank-bodied human entry whose KEY is a meaningful heading must not be
        # auto-removed (the key renders as the markdown heading).
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "Reduce onboarding friction", "", source="human")
            out = soul_integrity_check(td)
            empty = next(i for i in out["issues"] if i["code"] == "empty_entry")
            self.assertFalse(empty["repairable"])
            self.assertEqual(0, out["repairable_count"])

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
            _upsert(td, "GOALS.md", "g1", "  ", subject="acme", source="dreamer")
            self.assertEqual(0, soul_integrity_check(td, subject="self")["issue_count"])
            self.assertEqual(1, soul_integrity_check(td, subject="acme")["issue_count"])


class TestSoulIntegrityRepair(unittest.TestCase):
    def test_repair_removes_machine_empty_entry(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "real", "Reduce onboarding friction.")
            _upsert(td, "GOALS.md", "decay:stale", "", source="dreamer")
            out = soul_integrity_repair(td)
            self.assertTrue(out["ok"])
            self.assertEqual(1, out["repaired_count"])
            entries = current_soul_entries(td, file_name="GOALS.md")["entries"]
            self.assertIn("real", entries)
            self.assertNotIn("decay:stale", entries)
            # Idempotent: re-running finds nothing to repair.
            self.assertEqual(0, soul_integrity_repair(td)["repaired_count"])

    def test_repair_leaves_human_empty_entry(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "Reduce onboarding friction", "", source="human")
            out = soul_integrity_repair(td)
            self.assertEqual(0, out["repaired_count"])
            self.assertTrue(any(i["code"] == "empty_entry" for i in out["skipped"]))
            self.assertIn("Reduce onboarding friction",
                          current_soul_entries(td, file_name="GOALS.md")["entries"])

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "decay:x", "  ", source="dreamer")
            out = soul_integrity_repair(td, apply=False)
            self.assertFalse(out["applied"])
            self.assertEqual(0, out["repaired_count"])
            self.assertEqual(1, len(out["would_repair"]))
            self.assertIn("decay:x", current_soul_entries(td, file_name="GOALS.md")["entries"])

    def test_repair_leaves_non_repairable_issues(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "TENSIONS.md", "t1", "Speed versus accuracy.")
            _upsert(td, "TENSIONS.md", "t2", "Speed versus accuracy.")
            out = soul_integrity_repair(td)
            self.assertEqual(0, out["repaired_count"])
            self.assertTrue(any(i["code"] == "duplicate_content" for i in out["skipped"]))
            self.assertEqual(2, len(current_soul_entries(td, file_name="TENSIONS.md")["entries"]))


class TestRemoveIfUnchangedGuard(unittest.TestCase):
    def test_refuses_when_entry_changed_concurrently(self):
        # Simulate the check→repair race: capture the stale revision, then a
        # concurrent writer upserts real content. The guarded remove must refuse.
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "decay:x", "", source="dreamer")
            stale_rev = current_soul_entries(td, file_name="GOALS.md")["entries"]["decay:x"]["revision_id"]
            # Concurrent meaningful upsert for the same key.
            _upsert(td, "GOALS.md", "decay:x", "Now a real, endorsed goal.", source="human")
            out = remove_entry_if_unchanged(
                td, target_file="GOALS.md", entry_key="decay:x",
                expected_revision_id=stale_rev,
            )
            self.assertFalse(out["ok"])
            self.assertEqual("entry_changed", out["error"])
            # The newer content survived.
            self.assertEqual("Now a real, endorsed goal.",
                             current_soul_entries(td, file_name="GOALS.md")["entries"]["decay:x"]["content"])

    def test_refuses_when_entry_not_empty(self):
        with tempfile.TemporaryDirectory() as td:
            r = _upsert(td, "GOALS.md", "g", "Has content.")
            out = remove_entry_if_unchanged(
                td, target_file="GOALS.md", entry_key="g",
                expected_revision_id=r["revision_id"],
            )
            self.assertFalse(out["ok"])
            self.assertEqual("entry_not_empty", out["error"])

    def test_removes_when_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            _upsert(td, "GOALS.md", "decay:x", "", source="dreamer")
            rev = current_soul_entries(td, file_name="GOALS.md")["entries"]["decay:x"]["revision_id"]
            out = remove_entry_if_unchanged(
                td, target_file="GOALS.md", entry_key="decay:x", expected_revision_id=rev,
            )
            self.assertTrue(out["ok"])
            self.assertNotIn("decay:x", current_soul_entries(td, file_name="GOALS.md")["entries"])


if __name__ == "__main__":
    unittest.main()
