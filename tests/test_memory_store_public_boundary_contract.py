from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from core_memory.persistence.store import MemoryStore


pytestmark = pytest.mark.mixin_assembly


class TestMemoryStorePublicBoundaryContract(unittest.TestCase):
    def test_bead_query_link_recall_and_stats_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-boundary-") as td:
            with patch.dict(os.environ, {"CORE_MEMORY_GRAPH_BACKEND": "none"}, clear=False):
                store = MemoryStore(td)
                decision_id = store.add_bead(
                    type="decision",
                    title="Use canary deploy",
                    summary=["Roll out safely"],
                    because=["reduces release risk"],
                    detail="Captured as a boundary contract bead.",
                    session_id="s1",
                    source_turn_ids=["t1"],
                )
                lesson_id = store.add_bead(
                    type="lesson",
                    title="Canary deploy reduces blast radius",
                    summary=["A smaller rollout limits incident scope"],
                    because=["observed safer releases"],
                    session_id="s1",
                    source_turn_ids=["t2"],
                )

                decisions = store.query(type="decision", limit=10)
                self.assertIn(decision_id, {row.get("id") for row in decisions})

                assoc_id = store.link(
                    source_id=decision_id,
                    target_id=lesson_id,
                    relationship="supports",
                    explanation="lesson supports decision",
                    confidence=0.91,
                )
                self.assertTrue(assoc_id.startswith("assoc-"))
                self.assertTrue(store.recall(decision_id))

                stats = store.stats()
                self.assertEqual(2, stats.get("total_beads"))
                self.assertEqual(1, stats.get("total_associations"))
                self.assertEqual(1, stats.get("by_type", {}).get("decision"))

    def test_capture_turn_consolidate_and_metrics_state(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-boundary-") as td:
            store = MemoryStore(td)
            store.start_task_run("run-1", "task-1")
            store.capture_turn(role="user", content="Need rollout plan", session_id="s1")

            current = store.current_run_metrics()
            self.assertEqual("run-1", current.get("run_id"))
            self.assertEqual(1, current.get("turns_processed"))

            out = store.consolidate(session_id="s1")
            self.assertEqual("s1", out.get("session_id"))
            self.assertEqual(1, out.get("turns"))
            self.assertTrue(str(out.get("end_bead", "")).startswith("bead-"))

    def test_constraints_and_projection_rebuild_use_public_surfaces(self):
        with tempfile.TemporaryDirectory(prefix="cm-store-boundary-") as td:
            store = MemoryStore(td)
            principle_id = store.add_bead(
                type="design_principle",
                title="Deployments must use canary rollout",
                summary=["Use canary rollout for production deploys"],
                constraints=["must use canary rollout"],
                session_id="s1",
                source_turn_ids=["t1"],
            )

            active = store.active_constraints(limit=5)
            self.assertIn(principle_id, {row.get("bead_id") for row in active})

            checked = store.check_plan_constraints("deploy using canary rollout", limit=5)
            self.assertEqual("proceed", checked.get("recommendation"))
            self.assertEqual(1, checked.get("active_constraints"))

            rebuilt = store.rebuild_index_projection_from_sessions()
            self.assertTrue(rebuilt.get("ok"))
            self.assertGreaterEqual(rebuilt.get("total_beads", 0), 1)
            self.assertTrue((Path(td) / ".beads" / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
