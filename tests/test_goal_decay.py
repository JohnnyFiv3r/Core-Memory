import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.runtime.dreamer.goal_decay import (
    detect_goal_decay,
    enqueue_goal_decay_warnings,
)


def _age(idx_path, bead_id, days):
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    idx["beads"][bead_id]["created_at"] = old
    idx_path.write_text(json.dumps(idx), encoding="utf-8")


class TestGoalDecayDetection(unittest.TestCase):
    def test_dormant_goal_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="Stale goal", summary=["s"], goal_id="g1", session_id="s1")
            _age(Path(td) / ".beads" / "index.json", g, 60)  # past the 30d floor, no recall, low depth
            dets = detect_goal_decay(td)
            self.assertEqual(1, len(dets))
            self.assertEqual(g, dets[0]["goal_bead_id"])

    def test_fresh_goal_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(type="goal", title="New goal", summary=["s"], goal_id="g1", session_id="s1")
            self.assertEqual([], detect_goal_decay(td))  # age below floor

    def test_recalled_goal_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="Used goal", summary=["s"], goal_id="g1", session_id="s1")
            _age(Path(td) / ".beads" / "index.json", g, 60)
            store.recall(g)  # has traction → not dormant
            self.assertEqual([], detect_goal_decay(td))

    def test_resolved_goal_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="Done goal", summary=["s"], goal_id="g1", session_id="s1")
            idx_path = Path(td) / ".beads" / "index.json"
            _age(idx_path, g, 60)
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["beads"][g]["goal_status"] = "resolved"
            idx_path.write_text(json.dumps(idx), encoding="utf-8")
            self.assertEqual([], detect_goal_decay(td))

    def test_stale_days_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="G", summary=["s"], goal_id="g1", session_id="s1")
            _age(Path(td) / ".beads" / "index.json", g, 10)
            self.assertEqual([], detect_goal_decay(td))  # 10d < default 30d
            os.environ["CORE_MEMORY_GOAL_DECAY_STALE_DAYS"] = "5"
            try:
                self.assertEqual(1, len(detect_goal_decay(td)))  # 10d > 5d
            finally:
                del os.environ["CORE_MEMORY_GOAL_DECAY_STALE_DAYS"]


    def test_legacy_promoted_goal_not_flagged(self):
        # Legacy status:"promoted" must be treated as terminal (canonical helper).
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="Promoted legacy", summary=["s"], goal_id="g1", session_id="s1")
            idx_path = Path(td) / ".beads" / "index.json"
            _age(idx_path, g, 60)
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["beads"][g]["status"] = "promoted"  # legacy promotion encoding
            idx_path.write_text(json.dumps(idx), encoding="utf-8")
            self.assertEqual([], detect_goal_decay(td))

    def test_depth_scoring_covers_all_goal_beads(self):
        # Codex P2: ineligible goals before an eligible one must not truncate the
        # depth population — pass the total goal-bead count.
        from unittest.mock import patch
        import core_memory.runtime.dreamer.assembly_depth as ad

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            idx_path = Path(td) / ".beads" / "index.json"
            # Three superseded (ineligible) goals first, then one eligible stale goal.
            for i in range(3):
                sg = store.add_bead(type="goal", title=f"old{i}", summary=["s"], goal_id=f"o{i}", session_id="s1")
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
                idx["beads"][sg]["status"] = "superseded"
                idx_path.write_text(json.dumps(idx), encoding="utf-8")
            elig = store.add_bead(type="goal", title="eligible", summary=["s"], goal_id="e1", session_id="s1")
            _age(idx_path, elig, 60)

            real = ad.compute_assembly_depth
            seen = {}

            def _spy(root, *, target_kind="goal", limit=200):
                seen["limit"] = limit
                return real(root, target_kind=target_kind, limit=limit)

            with patch.object(ad, "compute_assembly_depth", _spy):
                detect_goal_decay(td)
            self.assertGreaterEqual(seen["limit"], 4)  # 3 superseded + 1 eligible


class TestGoalDecayEnqueue(unittest.TestCase):
    def test_enqueue_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            g = store.add_bead(type="goal", title="Stale", summary=["s"], goal_id="g1", session_id="s1")
            _age(Path(td) / ".beads" / "index.json", g, 60)
            first = enqueue_goal_decay_warnings(td)
            second = enqueue_goal_decay_warnings(td)
            self.assertEqual(1, first["enqueued"])
            self.assertEqual(0, second["enqueued"])
            rows = [r for r in _read_candidates(td) if r.get("hypothesis_type") == "goal_decay_warning"]
            self.assertEqual(1, len(rows))
            self.assertEqual("goal", rows[0]["proposal_family"])
            self.assertNotIn("source_bead_id", rows[0])  # not a myelination reward source

    def test_no_detection_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(root=td).add_bead(type="goal", title="fresh", summary=["s"], goal_id="g", session_id="s1")
            self.assertEqual(0, enqueue_goal_decay_warnings(td)["enqueued"])
            self.assertEqual([], _read_candidates(td))


class TestDreamerRunWiring(unittest.TestCase):
    def test_dreamer_run_invokes_tension_and_goal_decay(self):
        from core_memory.runtime.queue.side_effect_queue import process_side_effect_event

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            # A dormant goal (for decay) and a conflicting goal pair (for tension).
            g1 = store.add_bead(type="goal", title="Move fast", summary=["s"], goal_id="g1", session_id="s1")
            g2 = store.add_bead(type="goal", title="Avoid irreversible mistakes", summary=["s"], goal_id="g2", session_id="s2")
            store.link(g1, g2, "contradicts")
            _age(Path(td) / ".beads" / "index.json", g1, 60)

            out = process_side_effect_event(root=td, kind="dreamer-run", payload={"mode": "suggest"})
            self.assertTrue(out["ok"])
            # The run job surfaces both new finding families.
            self.assertIn("tension", out)
            self.assertIn("goal_decay", out)
            self.assertGreaterEqual(out["tension"].get("enqueued", 0), 1)
            self.assertGreaterEqual(out["goal_decay"].get("enqueued", 0), 1)
            kinds = {r.get("hypothesis_type") for r in _read_candidates(td)}
            self.assertIn("tension_candidate", kinds)
            self.assertIn("goal_decay_warning", kinds)


if __name__ == "__main__":
    unittest.main()
