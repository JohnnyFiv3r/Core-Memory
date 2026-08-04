from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.graph.edge_weights import DIRECTIONAL_RELS, RELATIONSHIP_HOP_WEIGHT
from core_memory.persistence.store import MemoryStore
from core_memory.policy.semantic_task_runtime import task_profile
from core_memory.runtime.associations.coverage import (
    apply_association_proposals,
    judge_association_candidates,
    list_association_candidates,
)
from core_memory.runtime.goals.progress import (
    backfill_goal_progress,
    process_goal_progress_event,
    run_goal_progress_tasks,
)
from core_memory.runtime.queue.side_effect_queue import process_side_effect_event, side_effect_queue_status
from core_memory.schema.normalization import CANONICAL_RELATION_TYPES, INFERENCE_CANONICAL_RELATION_TYPES
from core_memory.schema.semantic_tasks import (
    TASK_ASSOCIATION_DECISION,
    TASK_GOAL_PROGRESS,
    SemanticTaskRequest,
    SemanticTaskResult,
)
from core_memory.soul.goals import approve_goal


class FakeGoalProgressRuntime:
    def __init__(self, *, producer_decision: str = "propose", judge_action: str = "accept"):
        self.producer_decision = producer_decision
        self.judge_action = judge_action
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        if request.task_type == TASK_GOAL_PROGRESS:
            payload = request.payload
            if self.producer_decision == "no_link":
                output = {
                    "contract": "memory.goal_progress.v1",
                    "decision": "no_link",
                    "reason_text": "The evidence is relevant but does not demonstrate progress.",
                }
            else:
                output = {
                    "contract": "memory.goal_progress.v1",
                    "decision": "propose",
                    "source_bead_id": payload["source_bead_id"],
                    "target_goal_bead_id": payload["target_goal_bead_id"],
                    "relationship": "advances_goal",
                    "rationale": "The observed outcome satisfies a stated success criterion.",
                    "evidence_refs": [{"bead_id": payload["source_bead_id"]}],
                    "provenance_refs": [
                        {"bead_id": payload["source_bead_id"], "field": "result"}
                    ],
                    "temporal_bounds": {"observed_at": "2026-08-04T12:00:00+00:00"},
                    "confidence": 0.91,
                    "evaluator_version": payload["evaluator_version"],
                    "idempotency_key": payload["idempotency_key"],
                }
        elif request.task_type == TASK_ASSOCIATION_DECISION:
            candidate = request.payload["candidates"][0]
            source = candidate["source_bead"]
            direction = "source_to_target"
            relationship = "advances_goal"
            if self.judge_action == "invert":
                direction = "target_to_source"
            output = {
                "contract": "memory.association_judge.v2",
                "judge_model": "fake-goal-judge",
                "decisions": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "action": self.judge_action,
                        "relationship": relationship,
                        "direction": direction,
                        "confidence": 0.88,
                        "reason_text": "The source records measurable progress toward the goal.",
                        "truth_basis": "Observed outcome matched to an explicit goal criterion.",
                        "evidence_bead_ids": [source],
                        "evidence_refs": [{"bead_id": source, "field": "result"}],
                    }
                ],
                "reviewed_beads": [{"bead_id": source, "association_state": "linked"}],
            }
        else:
            raise AssertionError(f"unexpected task: {request.task_type}")
        return SemanticTaskResult(
            task_id=f"fake-{len(self.requests)}",
            task_type=request.task_type,
            ok=True,
            status="succeeded",
            output_json=output,
            prompt_version=request.prompt_version,
            rubric_version=request.rubric_version,
            output_schema=request.output_schema,
            authority_boundary=request.authority_boundary,
        )


def _fixture(root: str) -> tuple[MemoryStore, str, str]:
    store = MemoryStore(root=root)
    evidence_id = store.add_bead(
        type="outcome",
        title="Activation time fell below one day",
        summary=["Median activation time reached 18 hours."],
        detail="The August cohort activated in a median of 18 hours.",
        result="Activation time is now below the 24 hour target.",
        because=["The onboarding workflow was shortened."],
        session_id="s1",
        created_at="2026-08-04T12:00:00+00:00",
        retrieval_eligible=True,
    )
    goal_id = store.add_bead(
        type="goal",
        title="Reduce activation time",
        summary=["Reach median activation within one day."],
        because=["Faster activation improves retention."],
        goal_id="activation-speed",
        success_criteria=["Median activation time is below 24 hours."],
        session_id="s1",
        created_at="2026-08-01T12:00:00+00:00",
    )
    return store, evidence_id, goal_id


def _index(root: str) -> dict:
    return json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))


class TestGoalProgressContracts(unittest.TestCase):
    def test_advances_goal_is_canonical_directional_and_semantic_authored(self):
        self.assertIn("advances_goal", CANONICAL_RELATION_TYPES)
        self.assertIn("advances_goal", INFERENCE_CANONICAL_RELATION_TYPES)
        self.assertIn("advances_goal", DIRECTIONAL_RELS)
        self.assertGreater(RELATIONSHIP_HOP_WEIGHT["advances_goal"], 0)
        profile = task_profile(TASK_GOAL_PROGRESS)
        self.assertEqual("standard", profile.model_tier)
        self.assertEqual("semantic_author", profile.authority_boundary)

    def test_model_proposal_remains_pending_until_normal_judge_accepts(self):
        with tempfile.TemporaryDirectory() as td:
            _store, evidence_id, goal_id = _fixture(td)
            runtime = FakeGoalProgressRuntime()
            with patch("core_memory.runtime.goals.progress.get_semantic_task_runtime", return_value=runtime):
                proposed = run_goal_progress_tasks(
                    td,
                    evidence_bead_ids=[evidence_id],
                    goal_bead_ids=[goal_id],
                    judge=False,
                )
            self.assertTrue(proposed["ok"], proposed)
            self.assertEqual(1, proposed["counts"]["proposed"])
            self.assertFalse(any(a.get("relationship") == "advances_goal" for a in _index(td)["associations"]))
            candidate_id = proposed["candidate_ids"][0]

            with patch("core_memory.runtime.associations.coverage.get_semantic_task_runtime", return_value=runtime):
                judged = judge_association_candidates(td, candidate_ids=[candidate_id])
            self.assertTrue(judged["ok"], judged)
            edges = [a for a in _index(td)["associations"] if a.get("relationship") == "advances_goal"]
            self.assertEqual(1, len(edges))
            self.assertEqual(evidence_id, edges[0]["source_bead"])
            self.assertEqual(goal_id, edges[0]["target_bead"])
            self.assertEqual("goal_progress.v1", edges[0]["evaluator_version"])
            self.assertEqual(
                f"goal_advance:{goal_id}:{evidence_id}:goal_progress.v1",
                edges[0]["idempotency_key"],
            )
            self.assertTrue(edges[0]["provenance_refs"])
            self.assertTrue(edges[0]["temporal_bounds"])

    def test_goal_progress_judge_cannot_invert_the_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            _store, evidence_id, goal_id = _fixture(td)
            producer = FakeGoalProgressRuntime()
            with patch("core_memory.runtime.goals.progress.get_semantic_task_runtime", return_value=producer):
                proposed = run_goal_progress_tasks(
                    td,
                    evidence_bead_ids=[evidence_id],
                    goal_bead_ids=[goal_id],
                    judge=False,
                )
            judge = FakeGoalProgressRuntime(judge_action="invert")
            with patch("core_memory.runtime.associations.coverage.get_semantic_task_runtime", return_value=judge):
                out = judge_association_candidates(td, candidate_ids=proposed["candidate_ids"])
            self.assertEqual("quarantined", out["status"])
            self.assertFalse(any(a.get("relationship") == "advances_goal" for a in _index(td)["associations"]))
            candidate = next(
                row
                for row in list_association_candidates(td, limit=100)["results"]
                if row["candidate_id"] == proposed["candidate_ids"][0]
            )
            self.assertEqual("quarantined", candidate["status"])

    def test_host_advances_goal_proposal_uses_candidate_queue_not_direct_write(self):
        with tempfile.TemporaryDirectory() as td:
            _store, evidence_id, goal_id = _fixture(td)
            evaluator = "host-goal-progress.v1"
            out = apply_association_proposals(
                td,
                associations=[
                    {
                        "source_bead_id": evidence_id,
                        "target_bead_id": goal_id,
                        "relationship": "advances_goal",
                        "reason_text": "The observed result meets the goal threshold.",
                        "truth_basis": "Source-backed result and explicit success criterion.",
                        "confidence": 0.9,
                        "evidence_refs": [{"bead_id": evidence_id}],
                        "provenance_refs": [{"bead_id": evidence_id, "field": "result"}],
                        "temporal_bounds": {"observed_at": "2026-08-04T12:00:00+00:00"},
                        "evaluator_version": evaluator,
                        "idempotency_key": f"goal_advance:{goal_id}:{evidence_id}:{evaluator}",
                    }
                ],
                run_id="host-run-1",
            )
            self.assertTrue(out["ok"], out)
            self.assertEqual(1, out["pending_judge"])
            self.assertEqual([], out["association_ids"])
            self.assertFalse(any(a.get("relationship") == "advances_goal" for a in _index(td)["associations"]))
            candidates = list_association_candidates(td, status="pending_judge", limit=100)["results"]
            self.assertEqual(1, len([row for row in candidates if row.get("candidate_class") == "goal_progress"]))

    def test_cursor_backfill_prioritizes_support_seed_and_is_restartable(self):
        with tempfile.TemporaryDirectory() as td:
            store, evidence_id, goal_id = _fixture(td)
            other_id = store.add_bead(
                type="outcome",
                title="Support volume stayed flat",
                summary=["Support volume did not change."],
                detail="No measurable activation effect.",
                because=["The cohort was small."],
                session_id="s1",
                retrieval_eligible=True,
            )
            store.link(evidence_id, goal_id, "supports", explanation="Candidate evidence for the goal.")
            runtime = FakeGoalProgressRuntime(producer_decision="no_link")
            with patch("core_memory.runtime.goals.progress.get_semantic_task_runtime", return_value=runtime):
                first = backfill_goal_progress(td, limit=1)
                second = backfill_goal_progress(td, cursor=first["next_cursor"], limit=1)
            self.assertEqual("in_progress", first["status"])
            self.assertEqual("completed", second["status"])
            task_path = Path(td) / ".beads" / "events" / "goal-progress-tasks.jsonl"
            task_rows = [json.loads(line) for line in task_path.read_text().splitlines()]
            self.assertEqual(evidence_id, task_rows[0]["source_bead_id"])
            self.assertEqual({evidence_id, other_id}, {row["source_bead_id"] for row in task_rows})
            before = len(task_rows)
            with patch("core_memory.runtime.goals.progress.get_semantic_task_runtime", return_value=runtime):
                rerun = backfill_goal_progress(td, limit=10)
            self.assertEqual("completed", rerun["status"])
            after = len(task_path.read_text().splitlines())
            self.assertEqual(before, after)

    def test_bead_and_goal_state_hooks_enqueue_durable_goal_progress_work(self):
        with tempfile.TemporaryDirectory() as td:
            _store, _evidence_id, goal_id = _fixture(td)
            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            association_rows = [row for row in queue if row.get("kind") == "association-pass"]
            self.assertTrue(association_rows)
            self.assertTrue(any((row.get("payload") or {}).get("goal_progress") for row in association_rows))

            before = side_effect_queue_status(td)["by_kind"].get("goal-progress", 0)
            transitioned = approve_goal(
                td,
                bead_id=goal_id,
                actor="reviewer",
            )
            self.assertTrue(transitioned["ok"], transitioned)
            after = side_effect_queue_status(td)["by_kind"].get("goal-progress", 0)
            self.assertEqual(before + 1, after)

    def test_association_pass_materializes_the_live_goal_progress_hook(self):
        with tempfile.TemporaryDirectory() as td:
            _store, evidence_id, _goal_id = _fixture(td)
            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            association_event = next(
                row
                for row in queue
                if row.get("kind") == "association-pass"
                and evidence_id in ((row.get("payload") or {}).get("bead_ids") or [])
            )
            with patch(
                "core_memory.runtime.associations.coverage.run_association_coverage",
                return_value={"ok": True, "status": "linked"},
            ):
                out = process_side_effect_event(
                    root=td,
                    kind="association-pass",
                    payload=dict(association_event.get("payload") or {}),
                )
            self.assertTrue(out["ok"], out)
            self.assertEqual("goal-progress", (out.get("goal_progress") or {}).get("kind"), out)
            self.assertEqual(
                1,
                side_effect_queue_status(td)["by_kind"].get("goal-progress", 0),
            )

    def test_empty_scoped_pair_batch_does_not_expand_to_all_pairs(self):
        with tempfile.TemporaryDirectory() as td:
            _fixture(td)
            out = run_goal_progress_tasks(td, pair_rows=[], judge=False)
            self.assertTrue(out["ok"], out)
            self.assertEqual(0, out["counts"]["scanned"])

    def test_large_live_scan_enqueues_a_cursor_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            store, evidence_id, goal_id = _fixture(td)
            second_evidence_id = store.add_bead(
                type="outcome",
                title="Activation completion improved",
                summary=["Completion reached 84 percent."],
                result="More customers completed activation.",
                session_id="s2",
                retrieval_eligible=True,
            )
            runtime = FakeGoalProgressRuntime(producer_decision="no_link")
            with patch(
                "core_memory.runtime.goals.progress.get_semantic_task_runtime",
                return_value=runtime,
            ):
                out = process_goal_progress_event(
                    td,
                    {
                        "mode": "produce",
                        "evidence_bead_ids": [evidence_id, second_evidence_id],
                        "goal_bead_ids": [goal_id],
                        "trigger": "goal_active",
                        "max_pairs": 1,
                    },
                )
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["next_cursor"], out)
            continuation = out.get("continuation") or {}
            self.assertEqual("goal-progress", continuation.get("kind"), out)
            queue_path = Path(td) / ".beads" / "events" / "side-effects-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    row.get("kind") == "goal-progress"
                    and (row.get("payload") or {}).get("cursor") == out["next_cursor"]
                    for row in queue
                )
            )


if __name__ == "__main__":
    unittest.main()
