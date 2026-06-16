import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.soul.store import (
    SOUL_FILES,
    SOUL_REVISION_SCHEMA,
    approve_soul_update,
    list_soul_files,
    propose_soul_update,
    read_soul_file,
    reject_soul_update,
    soul_history,
)


class TestSoulProposeApprove(unittest.TestCase):
    def test_proposed_does_not_render_until_approved(self):
        with tempfile.TemporaryDirectory() as td:
            r = propose_soul_update(td, target_file="IDENTITY.md", entry_key="Observed Self",
                                    content="Builds bridges between systems.", source="dreamer",
                                    epistemic_status="inferred", requires_approval=True)
            self.assertTrue(r["ok"])
            self.assertEqual("proposed", r["status"])
            # Not yet folded into the projection.
            self.assertEqual(0, read_soul_file(td, file_name="IDENTITY.md")["entry_count"])
            ap = approve_soul_update(td, revision_id=r["revision_id"], approver="john")
            self.assertTrue(ap["ok"])
            self.assertEqual("applied", ap["status"])
            out = read_soul_file(td, file_name="IDENTITY.md")
            self.assertEqual(1, out["entry_count"])
            self.assertIn("Builds bridges", out["markdown"])
            self.assertIn("## Observed Self", out["markdown"])

    def test_auto_eligible_applies_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            r = propose_soul_update(td, target_file="GOALS.md", entry_key="g1",
                                    content="Reduce onboarding friction.", source="human",
                                    epistemic_status="observed", requires_approval=False)
            self.assertEqual("applied", r["status"])
            self.assertEqual(1, read_soul_file(td, file_name="GOALS.md")["entry_count"])

    def test_rejected_never_folds(self):
        with tempfile.TemporaryDirectory() as td:
            r = propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="x",
                                    requires_approval=True)
            rj = reject_soul_update(td, revision_id=r["revision_id"], reviewer="john", reason="wrong")
            self.assertTrue(rj["ok"])
            self.assertEqual("rejected", rj["status"])
            self.assertEqual(0, read_soul_file(td, file_name="GOALS.md")["entry_count"])

    def test_upsert_overwrites_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="v1", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="v2", requires_approval=False)
            out = read_soul_file(td, file_name="GOALS.md")
            self.assertEqual(1, out["entry_count"])
            self.assertIn("v2", out["markdown"])
            self.assertNotIn("v1", out["markdown"])

    def test_remove_op(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="v1", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", op="remove", requires_approval=False)
            self.assertEqual(0, read_soul_file(td, file_name="GOALS.md")["entry_count"])

    def test_double_decide_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="x", requires_approval=True)
            self.assertTrue(approve_soul_update(td, revision_id=r["revision_id"])["ok"])
            again = approve_soul_update(td, revision_id=r["revision_id"])
            self.assertFalse(again["ok"])
            self.assertEqual("already_decided", again["error"])

    def test_history_and_rendered_file_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary", content="We pursue continuity.",
                                requires_approval=False)
            hist = soul_history(td)
            self.assertGreaterEqual(hist["count"], 1)
            self.assertEqual(SOUL_REVISION_SCHEMA, hist["revisions"][0]["schema"])
            # Rendered markdown materialized under the identity dir.
            self.assertTrue((Path(td) / ".beads" / "identity" / "self" / "SOUL.md").exists())


class TestSoulValidationAndScope(unittest.TestCase):
    def test_invalid_target_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = propose_soul_update(td, target_file="NOTAFILE.md", entry_key="x", content="y")
            self.assertFalse(r["ok"])
            self.assertEqual("invalid_target_file", r["error"])

    def test_invalid_source_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(propose_soul_update(td, target_file="SOUL.md", entry_key="x", source="bot")["ok"])
            self.assertFalse(propose_soul_update(td, target_file="SOUL.md", entry_key="x", epistemic_status="vibes")["ok"])

    def test_subjects_are_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="self goal",
                                subject="self", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="org goal",
                                subject="acme", requires_approval=False)
            self.assertIn("self goal", read_soul_file(td, file_name="GOALS.md", subject="self")["markdown"])
            self.assertIn("org goal", read_soul_file(td, file_name="GOALS.md", subject="acme")["markdown"])
            self.assertNotIn("org goal", read_soul_file(td, file_name="GOALS.md", subject="self")["markdown"])

    def test_list_files(self):
        with tempfile.TemporaryDirectory() as td:
            out = list_soul_files(td)
            self.assertEqual(set(SOUL_FILES), {f["file_name"] for f in out["files"]})

    def test_does_not_write_beads(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="x", content="y", requires_approval=False)
            # SOUL is a projection layer — it never creates a bead index.
            self.assertFalse((Path(td) / ".beads" / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
