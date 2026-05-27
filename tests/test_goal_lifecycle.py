from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.session.goal_lifecycle import resolve_goals_for_turn


class TestGoalLifecycleResolution(unittest.TestCase):
    def _write_index(self, root: Path, index: dict) -> None:
        p = root / ".beads" / "index.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _write_session_row(self, root: Path, session_id: str, row: dict) -> None:
        p = root / ".beads" / f"session-{session_id}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_outcome_resolves_explicitly_visible_candidate_goal(self):
        with tempfile.TemporaryDirectory(prefix="cm-goal-lifecycle-") as td:
            root = Path(td)
            self._write_index(
                root,
                {
                    "beads": {
                        "goal-1": {
                            "id": "goal-1",
                            "type": "goal",
                            "title": "Ship semantic CLI ergonomics",
                            "summary": ["Add semantic status rebuild tail doctor"],
                            "tags": ["semantic"],
                            "session_id": "old-session",
                            "status": "open",
                            "promotion_state": "candidate",
                        },
                        "outcome-1": {
                            "id": "outcome-1",
                            "type": "outcome",
                            "title": "Semantic CLI shipped",
                            "summary": ["status rebuild tail doctor are implemented"],
                            "tags": ["semantic"],
                            "session_id": "s1",
                            "source_turn_ids": ["t1"],
                        },
                    },
                    "associations": [],
                },
            )
            self._write_session_row(root, "s1", {"id": "outcome-1", "session_id": "s1", "source_turn_ids": ["t1"]})

            out = resolve_goals_for_turn(
                root=str(root),
                session_id="s1",
                turn_id="t1",
                outcome_bead_id="outcome-1",
                visible_bead_ids=["outcome-1", "goal-1"],
            )

            self.assertTrue(out["ok"])
            self.assertEqual(1, out["resolved"])
            idx = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            goal = idx["beads"]["goal-1"]
            self.assertEqual("resolved", goal["status"])
            self.assertEqual("resolved", goal["promotion_state"])
            self.assertEqual("outcome-1", goal["resolved_by_bead_id"])
            assoc = idx["associations"][0]
            self.assertEqual("outcome-1", assoc["source_bead"])
            self.assertEqual("goal-1", assoc["target_bead"])
            self.assertEqual("resolves", assoc["relationship"])
            self.assertEqual("t1", assoc["turn_id"])
            self.assertEqual(["goal-1", "outcome-1"], assoc["evidence_bead_ids"])
            self.assertIn("grounding_hash", assoc)
            self.assertEqual(["goal-1", "outcome-1"], assoc["visible_bead_ids"])

    def test_cross_session_goal_must_be_explicitly_visible(self):
        with tempfile.TemporaryDirectory(prefix="cm-goal-lifecycle-") as td:
            root = Path(td)
            self._write_index(
                root,
                {
                    "beads": {
                        "goal-1": {
                            "id": "goal-1",
                            "type": "goal",
                            "title": "Ship semantic CLI ergonomics",
                            "summary": ["status rebuild tail doctor"],
                            "tags": ["semantic"],
                            "session_id": "old-session",
                            "status": "open",
                            "promotion_state": "candidate",
                        },
                        "outcome-1": {
                            "id": "outcome-1",
                            "type": "outcome",
                            "title": "Semantic CLI shipped",
                            "summary": ["status rebuild tail doctor"],
                            "tags": ["semantic"],
                            "session_id": "s1",
                        },
                    },
                    "associations": [],
                },
            )
            self._write_session_row(root, "s1", {"id": "outcome-1", "session_id": "s1", "source_turn_ids": ["t1"]})

            out = resolve_goals_for_turn(
                root=str(root),
                session_id="s1",
                turn_id="t1",
                outcome_bead_id="outcome-1",
                visible_bead_ids=["outcome-1"],
            )

            self.assertTrue(out["ok"])
            self.assertEqual(0, out["resolved"])
            idx = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual("candidate", idx["beads"]["goal-1"]["promotion_state"])
            self.assertEqual([], idx["associations"])


if __name__ == "__main__":
    unittest.main()
