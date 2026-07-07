import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.runtime.observability.myelination_rewards import read_reward_events
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

_ENV = {"CORE_MEMORY_MYELINATION_ENABLED": "1", "CORE_MEMORY_SEMANTIC_AUTODRAIN": "off"}


def _candidate(candidate_id: str, source_bead_id: str, target_bead_id: str, relationship: str = "supports") -> dict:
    return {
        "id": candidate_id,
        "status": "pending",
        "hypothesis_type": "goal_candidate",
        "goal_theme": "ship",
        "statement": "A goal candidate.",
        "source_bead_id": source_bead_id,
        "target_bead_id": target_bead_id,
        "relationship": relationship,
    }


def _dreamer_revision(root: str, candidate_id: str) -> str:
    out = propose_soul_update(
        root,
        target_file="GOALS.md",
        entry_key=f"goal:{candidate_id}",
        content="Dreamer surfaced a goal.",
        source="dreamer",
        epistemic_status="inferred",
        requires_approval=True,
        metadata={"dreamer_candidate_id": candidate_id},
    )
    return str(out["revision_id"])


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

    def test_distinct_unsafe_subjects_stay_isolated(self):
        # Codex P2: stripping chars must not merge distinct subjects.
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="email goal",
                                subject="alice@example.com", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="stripped goal",
                                subject="aliceexamplecom", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="domain goal",
                                subject="acme.com", requires_approval=False)
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="bare goal",
                                subject="acmecom", requires_approval=False)
            self.assertIn("email goal", read_soul_file(td, file_name="GOALS.md", subject="alice@example.com")["markdown"])
            self.assertIn("stripped goal", read_soul_file(td, file_name="GOALS.md", subject="aliceexamplecom")["markdown"])
            self.assertIn("domain goal", read_soul_file(td, file_name="GOALS.md", subject="acme.com")["markdown"])
            self.assertIn("bare goal", read_soul_file(td, file_name="GOALS.md", subject="acmecom")["markdown"])
            # No cross-contamination between the email and its stripped form.
            self.assertNotIn("stripped goal", read_soul_file(td, file_name="GOALS.md", subject="alice@example.com")["markdown"])

    def test_path_traversal_subject_is_contained(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="GOALS.md", entry_key="g", content="contained",
                                subject="../../etc", requires_approval=False)
            # The identity tree stays under .beads/identity (no escape).
            identity_root = Path(td) / ".beads" / "identity"
            self.assertTrue(identity_root.exists())
            for p in identity_root.rglob("revisions.jsonl"):
                self.assertIn(str(identity_root.resolve()), str(p.resolve()))

    def test_list_files(self):
        with tempfile.TemporaryDirectory() as td:
            out = list_soul_files(td)
            self.assertEqual(set(SOUL_FILES), {f["file_name"] for f in out["files"]})

    def test_does_not_write_beads(self):
        with tempfile.TemporaryDirectory() as td:
            propose_soul_update(td, target_file="SOUL.md", entry_key="x", content="y", requires_approval=False)
            # SOUL is a projection layer — it never creates a bead index.
            self.assertFalse((Path(td) / ".beads" / "index.json").exists())


class TestSoulDecisionRewards(unittest.TestCase):
    def test_approve_emits_positive_reward_for_dreamer_candidate_edge(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s")
            b = store.add_bead(type="goal", title="B", summary=["s"], detail="d", session_id="s")
            store.link(a, b, "supports")
            _write_candidates(td, [_candidate("dc-approve", a, b)])
            rid = _dreamer_revision(td, "dc-approve")

            out = approve_soul_update(td, revision_id=rid, approver="human")

            self.assertTrue(out["ok"])
            self.assertTrue(out["myelination_reward"]["ok"])
            rows = read_reward_events(td)
            self.assertEqual(1, len(rows))
            self.assertEqual("positive", rows[0]["polarity"])
            self.assertEqual("dreamer_candidate_decision", rows[0]["source_type"])
            self.assertEqual(["dc-approve"], rows[0]["supporting_candidate_ids"])

    def test_reject_emits_negative_reward_for_dreamer_candidate_edge(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s")
            b = store.add_bead(type="goal", title="B", summary=["s"], detail="d", session_id="s")
            store.link(a, b, "supports")
            _write_candidates(td, [_candidate("dc-reject", a, b)])
            rid = _dreamer_revision(td, "dc-reject")

            out = reject_soul_update(td, revision_id=rid, reviewer="human", reason="weak")

            self.assertTrue(out["ok"])
            self.assertTrue(out["myelination_reward"]["ok"])
            self.assertEqual("negative", read_reward_events(td)[0]["polarity"])

    def test_missing_edge_evidence_skips_reward_without_failing_decision(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            _write_candidates(td, [_candidate("dc-no-edge", "missing-a", "missing-b")])
            rid = _dreamer_revision(td, "dc-no-edge")

            out = approve_soul_update(td, revision_id=rid, approver="human")

            self.assertTrue(out["ok"])
            self.assertFalse(out["myelination_reward"]["ok"])
            self.assertEqual("no_concrete_edge", out["myelination_reward"]["skipped"])
            self.assertEqual([], read_reward_events(td))

    def test_decision_reward_is_not_replayed_after_double_decide(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV, clear=False):
            store = MemoryStore(root=td)
            a = store.add_bead(type="evidence", title="A", summary=["s"], detail="d", session_id="s")
            b = store.add_bead(type="goal", title="B", summary=["s"], detail="d", session_id="s")
            store.link(a, b, "supports")
            _write_candidates(td, [_candidate("dc-once", a, b)])
            rid = _dreamer_revision(td, "dc-once")

            self.assertTrue(approve_soul_update(td, revision_id=rid, approver="human")["ok"])
            again = approve_soul_update(td, revision_id=rid, approver="human")

            self.assertFalse(again["ok"])
            self.assertEqual("already_decided", again["error"])
            self.assertEqual(1, len(read_reward_events(td)))


if __name__ == "__main__":
    unittest.main()
