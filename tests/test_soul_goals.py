import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.goal_lifecycle_v2 import current_goal_status
from core_memory.persistence.store import MemoryStore
from core_memory.soul.goals import (
    abandon_goal,
    approve_goal,
    complete_goal,
    decay_goal,
    propose_goal,
    reject_goal,
)


def _status(root, bead_id):
    import json
    idx = json.loads((MemoryStore(root=root).beads_dir / "index.json").read_text(encoding="utf-8"))
    return current_goal_status(idx["beads"][bead_id])


class TestSoulGoals(unittest.TestCase):
    def test_propose_creates_candidate_goal(self):
        with tempfile.TemporaryDirectory() as td:
            out = propose_goal(td, title="Reduce onboarding friction", statement="Make setup < 5 min")
            self.assertTrue(out["ok"])
            self.assertEqual("candidate", out["status"])
            self.assertTrue(out["goal_id"])
            self.assertEqual("candidate", _status(td, out["bead_id"]))

    def test_propose_requires_title(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(propose_goal(td, title="  ")["ok"])

    def test_approve_then_complete_by_goal_id(self):
        with tempfile.TemporaryDirectory() as td:
            p = propose_goal(td, title="Ship v1", goal_id="g-ship")
            self.assertTrue(approve_goal(td, goal_id="g-ship", actor="human")["ok"])
            self.assertEqual("endorsed", _status(td, p["bead_id"]))
            self.assertTrue(complete_goal(td, goal_id="g-ship")["ok"])
            self.assertEqual("completed", _status(td, p["bead_id"]))

    def test_reject_abandons_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            p = propose_goal(td, title="Maybe", goal_id="g-maybe")
            out = reject_goal(td, goal_id="g-maybe", reason="out of scope")
            self.assertTrue(out["ok"])
            self.assertEqual("abandoned", _status(td, p["bead_id"]))

    def test_decay_and_revive(self):
        with tempfile.TemporaryDirectory() as td:
            p = propose_goal(td, title="Long horizon", goal_id="g-lh")
            approve_goal(td, goal_id="g-lh")
            self.assertTrue(decay_goal(td, goal_id="g-lh")["ok"])
            self.assertEqual("decaying", _status(td, p["bead_id"]))
            self.assertTrue(approve_goal(td, goal_id="g-lh")["ok"])  # decaying -> endorsed
            self.assertEqual("endorsed", _status(td, p["bead_id"]))

    def test_abandon_by_bead_id(self):
        with tempfile.TemporaryDirectory() as td:
            p = propose_goal(td, title="Drop me")
            out = abandon_goal(td, bead_id=p["bead_id"], actor="human")
            self.assertTrue(out["ok"])
            self.assertEqual("abandoned", _status(td, p["bead_id"]))

    def test_invalid_transition_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            propose_goal(td, title="x", goal_id="g-x")
            out = complete_goal(td, goal_id="g-x")  # candidate -> completed not allowed
            self.assertFalse(out["ok"])
            self.assertEqual("invalid_transition", out["error"])

    def test_unknown_goal_returns_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            out = approve_goal(td, goal_id="nope")
            self.assertFalse(out["ok"])
            self.assertEqual("goal_not_found", out["error"])

    def test_subject_scoped_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            a = propose_goal(td, title="acme goal", subject="acme")
            s = propose_goal(td, title="self goal", subject="self")
            store = MemoryStore(root=td)
            import json
            idx = json.loads((store.beads_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("soul-goals:acme", idx["beads"][a["bead_id"]]["session_id"])
            self.assertEqual("soul-goals:self", idx["beads"][s["bead_id"]]["session_id"])

    def test_same_goal_id_isolated_across_subjects(self):
        # Two subjects reuse goal_id "g"; an action for one must resolve to and
        # mutate only that subject's bead (P2: subject-scoped resolution).
        with tempfile.TemporaryDirectory() as td:
            a = propose_goal(td, title="acme goal", goal_id="g", subject="acme")
            s = propose_goal(td, title="self goal", goal_id="g", subject="self")
            self.assertNotEqual(a["bead_id"], s["bead_id"])

            self.assertTrue(approve_goal(td, goal_id="g", subject="acme")["ok"])
            self.assertEqual("endorsed", _status(td, a["bead_id"]))
            self.assertEqual("candidate", _status(td, s["bead_id"]))  # untouched

            self.assertTrue(abandon_goal(td, goal_id="g", subject="self")["ok"])
            self.assertEqual("abandoned", _status(td, s["bead_id"]))
            self.assertEqual("endorsed", _status(td, a["bead_id"]))  # still untouched

    def test_action_wrong_subject_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            propose_goal(td, title="acme goal", goal_id="g", subject="acme")
            out = approve_goal(td, goal_id="g", subject="self")  # no such goal for self
            self.assertFalse(out["ok"])
            self.assertEqual("goal_not_found", out["error"])


if __name__ == "__main__":
    unittest.main()
