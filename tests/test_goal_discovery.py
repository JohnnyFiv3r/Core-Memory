import os
import tempfile
import unittest

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _read_candidates
from core_memory.runtime.dreamer.goal_discovery import (
    detect_latent_goals,
    enqueue_latent_goal_candidates,
)


def _decision(store, title, topics, session):
    return store.add_bead(type="decision", title=title, summary=["s"], because=["x"],
                          detail="d", topics=topics, session_id=session)


class TestLatentGoalDetection(unittest.TestCase):
    def test_recurring_theme_across_sessions_detected(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "Chose explainable model", ["explainability"], "s1")
            _decision(store, "Rejected opaque vendor", ["explainability"], "s2")
            _decision(store, "Documented decision trail", ["explainability"], "s3")
            dets = detect_latent_goals(td)
            self.assertEqual(1, len(dets))
            self.assertEqual("explainability", dets[0]["theme"])
            self.assertEqual(3, dets[0]["occurrence_count"])
            self.assertEqual(3, dets[0]["session_count"])

    def test_single_session_loop_not_detected(self):
        # All occurrences in one session = repetition loop, not distributed.
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for i in range(4):
                _decision(store, f"d{i}", ["explainability"], "s1")
            self.assertEqual([], detect_latent_goals(td))

    def test_below_occurrence_threshold_not_detected(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "d1", ["explainability"], "s1")
            _decision(store, "d2", ["explainability"], "s2")  # only 2 < default 3
            self.assertEqual([], detect_latent_goals(td))

    def test_theme_already_a_goal_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            store.add_bead(type="goal", title="Improve explainability", summary=["s"],
                           goal_id="g1", session_id="s0")
            _decision(store, "d1", ["explainability"], "s1")
            _decision(store, "d2", ["explainability"], "s2")
            _decision(store, "d3", ["explainability"], "s3")
            self.assertEqual([], detect_latent_goals(td))  # goal already covers it

    def test_env_thresholds(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            _decision(store, "d1", ["speed"], "s1")
            _decision(store, "d2", ["speed"], "s2")
            self.assertEqual([], detect_latent_goals(td))
            os.environ["CORE_MEMORY_GOAL_DISCOVERY_MIN_OCCURRENCES"] = "2"
            try:
                self.assertEqual(1, len(detect_latent_goals(td)))
            finally:
                del os.environ["CORE_MEMORY_GOAL_DISCOVERY_MIN_OCCURRENCES"]


class TestLatentGoalEnqueue(unittest.TestCase):
    def test_enqueue_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for s in ("s1", "s2", "s3"):
                _decision(store, f"d-{s}", ["explainability"], s)
            first = enqueue_latent_goal_candidates(td)
            second = enqueue_latent_goal_candidates(td)
            self.assertEqual(1, first["enqueued"])
            self.assertEqual(0, second["enqueued"])
            rows = [r for r in _read_candidates(td) if r.get("hypothesis_type") == "goal_candidate"]
            self.assertEqual(1, len(rows))
            self.assertEqual("explainability", rows[0]["goal_theme"])
            self.assertNotIn("source_bead_id", rows[0])  # not a myelination reward source

    def test_no_detection_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            _decision(MemoryStore(root=td), "d", ["x"], "s1")
            self.assertEqual(0, enqueue_latent_goal_candidates(td)["enqueued"])
            self.assertEqual([], _read_candidates(td))

    def test_dreamer_run_invokes_goal_discovery(self):
        from core_memory.runtime.queue.side_effect_queue import process_side_effect_event

        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            for s in ("s1", "s2", "s3"):
                _decision(store, f"d-{s}", ["explainability"], s)
            out = process_side_effect_event(root=td, kind="dreamer-run", payload={"mode": "suggest"})
            self.assertIn("goal_discovery", out)
            self.assertGreaterEqual(out["goal_discovery"].get("enqueued", 0), 1)
            kinds = {r.get("hypothesis_type") for r in _read_candidates(td)}
            self.assertIn("goal_candidate", kinds)


if __name__ == "__main__":
    unittest.main()
