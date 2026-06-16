import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.engine import process_session_start
from core_memory.soul.injection import soul_injection
from core_memory.soul.store import approve_soul_update, propose_soul_update, reject_soul_update


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
            self.assertIn("inferred", out["epistemic_groups"])

    def test_injection_groups_entries_by_epistemic_status(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="IDENTITY.md", entry_key="endorsed",
                                content="Approved operating identity.", epistemic_status="endorsed",
                                requires_approval=False)
            propose_soul_update(td, target_file="SOUL.md", entry_key="observed",
                                content="Repeatedly chooses simpler designs.", epistemic_status="observed",
                                requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="inferred",
                                content="Likely wants lower maintenance load.", epistemic_status="inferred",
                                requires_approval=False)
            out = soul_injection(td, include=("SOUL.md", "GOALS.md", "IDENTITY.md"))
            groups = out["epistemic_groups"]
            self.assertEqual(["Approved operating identity."], [r["content"] for r in groups["endorsed"]])
            self.assertEqual(["Repeatedly chooses simpler designs."], [r["content"] for r in groups["observed"]])
            self.assertEqual(["Likely wants lower maintenance load."], [r["content"] for r in groups["inferred"]])

    def test_proposed_and_rejected_revisions_are_not_injected(self):
        with tempfile.TemporaryDirectory() as td:
            proposed = propose_soul_update(td, target_file="SOUL.md", entry_key="draft",
                                           content="Draft self-model.", requires_approval=True)
            rejected = propose_soul_update(td, target_file="SOUL.md", entry_key="bad",
                                           content="Rejected self-model.", requires_approval=True)
            reject_soul_update(td, revision_id=str(rejected["revision_id"]), reason="not grounded")
            approved = propose_soul_update(td, target_file="SOUL.md", entry_key="live",
                                           content="Approved self-model.", requires_approval=True)
            approve_soul_update(td, revision_id=str(approved["revision_id"]), approver="reviewer")
            out = soul_injection(td)
            text = out["files"]["SOUL.md"]
            self.assertIn("Approved self-model.", text)
            self.assertNotIn("Draft self-model.", text)
            self.assertNotIn("Rejected self-model.", text)
            self.assertNotIn(str(proposed["revision_id"]), text)

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


class TestSoulInjectionText(unittest.TestCase):
    def test_empty_text(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory.soul.injection import soul_injection_text
            self.assertEqual("", soul_injection_text(td))

    def test_text_has_header_and_content(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory.soul.injection import soul_injection_text, SOUL_INJECTION_HEADER
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="We pursue continuity.", requires_approval=False)
            txt = soul_injection_text(td)
            self.assertTrue(txt.startswith(SOUL_INJECTION_HEADER))
            self.assertIn("We pursue continuity.", txt)
            self.assertIn("## Inferred", txt)

    def test_text_visibly_separates_epistemic_sections(self):
        with tempfile.TemporaryDirectory() as td:
            from core_memory.soul.injection import soul_injection_text
            propose_soul_update(td, target_file="IDENTITY.md", entry_key="e",
                                content="Endorsed identity.", epistemic_status="endorsed",
                                requires_approval=False)
            propose_soul_update(td, target_file="SOUL.md", entry_key="o",
                                content="Observed pattern.", epistemic_status="observed",
                                requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="i",
                                content="Inferred direction.", epistemic_status="inferred",
                                requires_approval=False)
            txt = soul_injection_text(td)
            self.assertLess(txt.index("## Endorsed"), txt.index("## Observed"))
            self.assertLess(txt.index("## Observed"), txt.index("## Inferred"))
            self.assertIn("Endorsed identity.", txt)
            self.assertIn("Observed pattern.", txt)
            self.assertIn("Inferred direction.", txt)


class TestAdapterPromptCarriesSoul(unittest.TestCase):
    def test_pydanticai_continuity_prompt_includes_soul(self):
        # Codex P2: SOUL must reach the rendered adapter prompt, not just the
        # discarded process_session_start return.
        from core_memory.integrations.pydanticai.memory_tools import continuity_prompt

        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="Builds continuity systems.", requires_approval=False)
            prompt = continuity_prompt(root=td, session_id="s1")
            self.assertIn("Builds continuity systems.", prompt)
            self.assertIn("Self-Model (SOUL)", prompt)
            self.assertIn("## Inferred", prompt)

    def test_pydanticai_continuity_prompt_soul_only_when_no_records(self):
        from core_memory.integrations.pydanticai.memory_tools import continuity_prompt, CONTINUITY_EMPTY

        with tempfile.TemporaryDirectory() as td:
            # No continuity records at all, but a SOUL exists.
            propose_soul_update(td, target_file="GOALS.md", entry_key="g",
                                content="Reduce friction.", requires_approval=False)
            prompt = continuity_prompt(root=td, session_id="s1", ensure_session_start=False)
            self.assertNotEqual(CONTINUITY_EMPTY, prompt)
            self.assertIn("Reduce friction.", prompt)

    def test_langchain_memory_includes_grouped_soul_text(self):
        from tests.test_langchain_adapter_contract import _fake_langchain_core_modules

        with tempfile.TemporaryDirectory() as td, _fake_langchain_core_modules():
            import importlib

            propose_soul_update(td, target_file="SOUL.md", entry_key="Summary",
                                content="Carries grouped self-model.", epistemic_status="observed",
                                requires_approval=False)
            m = importlib.import_module("core_memory.integrations.langchain.memory")
            CoreMemory = m.CoreMemory
            cm = CoreMemory(root=td, session_id="lc-soul", memory_key="memory")
            payload = cm.load_memory_variables({})
            self.assertIn("Carries grouped self-model.", payload["memory"])
            self.assertIn("## Observed", payload["memory"])


if __name__ == "__main__":
    unittest.main()
