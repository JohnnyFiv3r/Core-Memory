import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.engine import process_session_start
from core_memory.soul.injection import soul_injection
from core_memory.soul.store import propose_soul_update


class TestSoulInjection(unittest.TestCase):
    def test_empty_soul_injects_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            out = soul_injection(td)
            self.assertTrue(out["ok"])
            self.assertFalse(out["present"])
            self.assertEqual({}, out["files"])

    def test_injects_only_nonempty_files(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="We pursue continuity.", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g1",
                                content="Reduce onboarding friction.", requires_approval=False)
            # TENSIONS.md left empty.
            out = soul_injection(td)
            self.assertTrue(out["present"])
            self.assertEqual(["GOALS.md", "SOUL.md"], out["injected_files"])
            self.assertIn("continuity", out["files"]["SOUL.md"])
            self.assertNotIn("TENSIONS.md", out["files"])

    def test_injection_is_subject_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="s", content="self identity",
                                subject="self", requires_approval=False)
            propose_soul_update(td, target_file="SOUL.md", entry_key="s", content="org identity",
                                subject="acme", requires_approval=False)
            self.assertIn("self identity", soul_injection(td, subject="self")["files"]["SOUL.md"])
            self.assertIn("org identity", soul_injection(td, subject="acme")["files"]["SOUL.md"])


class TestSessionStartCarriesSoul(unittest.TestCase):
    def test_session_start_includes_soul_payload(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="Builds continuity systems.", requires_approval=False)
            out = process_session_start(root=td, session_id="s1")
            self.assertTrue(out["ok"])
            self.assertIn("soul", out)
            self.assertTrue(out["soul"]["present"])
            self.assertIn("SOUL.md", out["soul"]["files"])

    def test_soul_present_on_repeated_session_start(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="x", requires_approval=False)
            first = process_session_start(root=td, session_id="s1")
            self.assertTrue(first["created"])
            # SOUL evolves after the snapshot bead exists; re-calling still injects current SOUL.
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="new goal",
                                requires_approval=False)
            second = process_session_start(root=td, session_id="s1")
            self.assertFalse(second["created"])  # snapshot bead reused
            self.assertIn("GOALS.md", second["soul"]["files"])  # fresh SOUL, not stale

    def test_session_start_soul_subject_param(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="s", content="acme self",
                                subject="acme", requires_approval=False)
            out = process_session_start(root=td, session_id="s1", soul_subject="acme")
            self.assertIn("acme self", out["soul"]["files"]["SOUL.md"])


if __name__ == "__main__":
    unittest.main()
