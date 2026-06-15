import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.runtime.dreamer.tension_discovery import (
    detect_goal_conflicts,
    enqueue_goal_conflict_candidates,
)


def _two_conflicting_goals(store):
    g1 = store.add_bead(type="goal", title="Move fast", summary=["s"], goal_id="g1", session_id="s1")
    g2 = store.add_bead(type="goal", title="Avoid irreversible mistakes", summary=["s"], goal_id="g2", session_id="s2")
    store.link(g1, g2, "contradicts")
    return g1, g2


class TestGoalConflictDetection(unittest.TestCase):
    def test_detects_active_goal_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1, g2 = _two_conflicting_goals(store)
            dets = detect_goal_conflicts(td)
            self.assertEqual(1, len(dets))
            d = dets[0]
            self.assertEqual("goal_conflict", d["tension_kind"])
            self.assertEqual({g1, g2}, {d["conflict_bead_a"], d["conflict_bead_b"]})
            self.assertIn(g1, d["assembly_depth"])
            self.assertIn(g2, d["assembly_depth"])

    def test_no_conflict_without_contradicts_edge(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1 = store.add_bead(type="goal", title="A", summary=["s"], goal_id="g1", session_id="s1")
            g2 = store.add_bead(type="goal", title="B", summary=["s"], goal_id="g2", session_id="s2")
            store.link(g1, g2, "associated_with")  # not a contradiction
            self.assertEqual([], detect_goal_conflicts(td))

    def test_contradicts_between_non_goals_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            d1 = store.add_bead(type="decision", title="D1", summary=["s"], because=["x"], detail="d", session_id="s1")
            d2 = store.add_bead(type="decision", title="D2", summary=["s"], because=["y"], detail="d", session_id="s2")
            store.link(d1, d2, "contradicts")
            self.assertEqual([], detect_goal_conflicts(td))

    def test_inactive_contradicts_edge_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1, g2 = _two_conflicting_goals(store)
            idx_path = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            for a in idx["associations"]:
                if a.get("relationship") == "contradicts":
                    a["status"] = "retracted"
            idx_path.write_text(json.dumps(idx), encoding="utf-8")
            self.assertEqual([], detect_goal_conflicts(td))

    def test_superseded_goal_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1, g2 = _two_conflicting_goals(store)
            idx_path = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["beads"][g2]["status"] = "superseded"
            idx_path.write_text(json.dumps(idx), encoding="utf-8")
            self.assertEqual([], detect_goal_conflicts(td))


class TestTensionCandidateEnqueue(unittest.TestCase):
    def test_enqueues_tension_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g1, g2 = _two_conflicting_goals(store)
            out = enqueue_goal_conflict_candidates(td)
            self.assertEqual(1, out["enqueued"])
            rows = [r for r in _read_candidates(td) if r.get("hypothesis_type") == "tension_candidate"]
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("pending", row["status"])
            self.assertEqual("tension", row["proposal_family"])
            # Stays out of the myelination reward path: no source/relationship fields.
            self.assertNotIn("source_bead_id", row)
            self.assertNotIn("relationship", row)
            self.assertEqual({g1, g2}, set(row["supporting_bead_ids"]))

    def test_enqueue_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _two_conflicting_goals(store)
            first = enqueue_goal_conflict_candidates(td)
            second = enqueue_goal_conflict_candidates(td)
            self.assertEqual(1, first["enqueued"])
            self.assertEqual(0, second["enqueued"])
            rows = [r for r in _read_candidates(td) if r.get("hypothesis_type") == "tension_candidate"]
            self.assertEqual(1, len(rows))

    def test_no_detection_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(root=td).add_bead(type="goal", title="lonely", summary=["s"], goal_id="g", session_id="s1")
            out = enqueue_goal_conflict_candidates(td)
            self.assertEqual(0, out["enqueued"])
            self.assertEqual([], _read_candidates(td))


if __name__ == "__main__":
    unittest.main()
