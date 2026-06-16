import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.goal_lifecycle_v2 import (
    TERMINAL_GOAL_STATES,
    current_goal_status,
    transition_goal_state_for_store,
)
from core_memory.runtime.dreamer.goal_filters import is_active_goal


def _goal(store, gid="g1"):
    return store.add_bead(type="goal", title=f"Goal {gid}", summary=["s"],
                          goal_id=gid, because=["x"], session_id="s1")


def _bead(store, bead_id):
    import json
    idx = json.loads((store.beads_dir / "index.json").read_text(encoding="utf-8"))
    return idx["beads"][bead_id]


class TestGoalLifecycleTransitions(unittest.TestCase):
    def test_full_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            self.assertEqual("candidate", current_goal_status(_bead(store, gid)))
            for nxt in ("endorsed", "active", "completed"):
                out = transition_goal_state_for_store(store, goal_bead_id=gid, to_state=nxt, actor="human")
                self.assertTrue(out["ok"], out)
                self.assertEqual(nxt, out["to_state"])
            b = _bead(store, gid)
            self.assertEqual("completed", current_goal_status(b))
            self.assertEqual("completed", b["status"])  # terminal closes status

    def test_invalid_transition_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            out = transition_goal_state_for_store(store, goal_bead_id=gid, to_state="completed")
            self.assertFalse(out["ok"])
            self.assertEqual("invalid_transition", out["error"])
            self.assertIn("endorsed", out["allowed"])

    def test_terminal_is_frozen(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            transition_goal_state_for_store(store, goal_bead_id=gid, to_state="abandoned")
            out = transition_goal_state_for_store(store, goal_bead_id=gid, to_state="active")
            self.assertFalse(out["ok"])
            self.assertEqual("goal_terminal", out["error"])

    def test_decaying_is_revivable(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            transition_goal_state_for_store(store, goal_bead_id=gid, to_state="endorsed")
            transition_goal_state_for_store(store, goal_bead_id=gid, to_state="decaying")
            out = transition_goal_state_for_store(store, goal_bead_id=gid, to_state="active")
            self.assertTrue(out["ok"])
            self.assertEqual("active", current_goal_status(_bead(store, gid)))

    def test_invalid_target_state(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            for bad in ("resolved", "candidate", "frobnicate"):
                out = transition_goal_state_for_store(store, goal_bead_id=gid, to_state=bad)
                self.assertFalse(out["ok"])
                self.assertEqual("invalid_target_state", out["error"])

    def test_not_goal_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            note = store.add_bead(type="decision", title="d", summary=["s"], because=["x"],
                                  detail="d", session_id="s1")
            self.assertEqual("not_goal", transition_goal_state_for_store(
                store, goal_bead_id=note, to_state="endorsed")["error"])
            self.assertIn("bead_not_found", transition_goal_state_for_store(
                store, goal_bead_id="nope", to_state="endorsed")["error"])

    def test_audit_log_appended(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            gid = _goal(store)
            transition_goal_state_for_store(store, goal_bead_id=gid, to_state="endorsed", reason="human approved")
            log = store.beads_dir / "events" / "promotion-decisions.jsonl"
            self.assertTrue(log.exists())
            self.assertIn("goal_endorsed", log.read_text(encoding="utf-8"))


class TestCurrentGoalStatusLegacy(unittest.TestCase):
    def test_legacy_resolved_status_maps(self):
        self.assertEqual("resolved", current_goal_status({"status": "resolved"}))
        self.assertEqual("resolved", current_goal_status({"goal_status": "resolved"}))
        self.assertEqual("candidate", current_goal_status({"status": "open"}))
        self.assertEqual("candidate", current_goal_status({}))


class TestIsActiveGoalIntegration(unittest.TestCase):
    def test_terminal_states_excluded(self):
        for terminal in TERMINAL_GOAL_STATES:
            self.assertFalse(is_active_goal({"type": "goal", "goal_status": terminal}), terminal)

    def test_open_and_decaying_states_active(self):
        for live in ("candidate", "endorsed", "active", "decaying"):
            self.assertTrue(is_active_goal({"type": "goal", "goal_status": live}), live)


if __name__ == "__main__":
    unittest.main()
