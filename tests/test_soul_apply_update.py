import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.soul.store import (
    apply_soul_update,
    propose_soul_update,
    read_soul_file,
    soul_history,
)


def _pending(root, **kw):
    return propose_soul_update(root, requires_approval=True, **kw)


class TestApplySoulUpdate(unittest.TestCase):
    def test_auto_applies_inferred_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            p = _pending(td, target_file="SOUL.md", entry_key="s", content="inferred summary",
                         source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=p["revision_id"], applied_by="agent")
            self.assertTrue(out["ok"])
            self.assertEqual("applied", out["status"])
            self.assertIn("inferred summary", read_soul_file(td, file_name="SOUL.md")["markdown"])

    def test_refuses_endorsed_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            p = _pending(td, target_file="IDENTITY.md", entry_key="Endorsed self",
                         content="We are operator-first.", source="agent", epistemic_status="endorsed")
            out = apply_soul_update(td, revision_id=p["revision_id"])
            self.assertFalse(out["ok"])
            self.assertEqual("requires_human_approval", out["error"])
            self.assertEqual("endorsed_meaning", out["reason"])
            # Not applied.
            self.assertNotIn("operator-first", read_soul_file(td, file_name="IDENTITY.md")["markdown"])

    def test_refuses_removing_human_entry(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="Human goal.",
                                source="human", epistemic_status="endorsed", requires_approval=False)
            rm = _pending(td, target_file="GOALS.md", entry_key="g1", op="remove",
                          source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=rm["revision_id"])
            self.assertFalse(out["ok"])
            self.assertEqual("removes_human_entry", out["reason"])
            self.assertIn("Human goal.", read_soul_file(td, file_name="GOALS.md")["markdown"])

    def test_unknown_or_decided_revision(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(apply_soul_update(td, revision_id="nope")["ok"])
            p = _pending(td, target_file="SOUL.md", entry_key="s", content="x",
                         source="agent", epistemic_status="inferred")
            apply_soul_update(td, revision_id=p["revision_id"])
            # second apply blocked (already decided)
            second = apply_soul_update(td, revision_id=p["revision_id"])
            self.assertFalse(second["ok"])
            self.assertEqual("already_decided", second["error"])


if __name__ == "__main__":
    unittest.main()
