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
                                source="human", epistemic_status="inferred", requires_approval=False)
            rm = _pending(td, target_file="GOALS.md", entry_key="g1", op="remove",
                          source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=rm["revision_id"])
            self.assertFalse(out["ok"])
            self.assertEqual("protected_entry", out["reason"])
            self.assertIn("Human goal.", read_soul_file(td, file_name="GOALS.md")["markdown"])

    def test_refuses_overwriting_human_entry_via_upsert(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1", content="Human goal.",
                                source="human", epistemic_status="inferred", requires_approval=False)
            up = _pending(td, target_file="GOALS.md", entry_key="g1", content="Agent overwrite.",
                          source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=up["revision_id"])
            self.assertFalse(out["ok"])
            self.assertEqual("protected_entry", out["reason"])
            md = read_soul_file(td, file_name="GOALS.md")["markdown"]
            self.assertIn("Human goal.", md)
            self.assertNotIn("Agent overwrite.", md)

    def test_refuses_removing_endorsed_agent_sourced_entry(self):
        # An endorsed entry whose stored source is "agent" (e.g. human-approved an
        # agent proposal) must still be protected from auto-removal.
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="IDENTITY.md", entry_key="v1", content="Endorsed value.",
                                source="agent", epistemic_status="endorsed", requires_approval=False)
            rm = _pending(td, target_file="IDENTITY.md", entry_key="v1", op="remove",
                          source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=rm["revision_id"])
            self.assertFalse(out["ok"])
            self.assertEqual("protected_entry", out["reason"])
            self.assertIn("Endorsed value.", read_soul_file(td, file_name="IDENTITY.md")["markdown"])

    def test_auto_overwrite_of_agent_inferred_entry_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="s", content="old inferred",
                                source="agent", epistemic_status="inferred", requires_approval=False)
            up = _pending(td, target_file="SOUL.md", entry_key="s", content="new inferred",
                          source="agent", epistemic_status="inferred")
            out = apply_soul_update(td, revision_id=up["revision_id"])
            self.assertTrue(out["ok"])
            self.assertIn("new inferred", read_soul_file(td, file_name="SOUL.md")["markdown"])

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
