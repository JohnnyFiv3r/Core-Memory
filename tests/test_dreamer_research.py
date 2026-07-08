from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates
from core_memory.runtime.dreamer.research import run_dreamer_research
from core_memory.runtime.queue.side_effect_queue import process_side_effect_event
from core_memory.persistence.semantic_task_receipts import list_semantic_task_runs, record_semantic_task_run
from core_memory.schema.semantic_tasks import SemanticTaskRequest, SemanticTaskResult


class FakeDreamerResearchRuntime:
    def __init__(self, output_json: dict, verifier_json: dict | None = None):
        self.output_json = output_json
        self.verifier_json = verifier_json or {
            "contract": "memory.semantic_task_verifier.v1",
            "decision": "pass",
            "warnings": [],
            "blocking_errors": [],
        }
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        output_json = self.verifier_json if request.task_type == "verifier" else self.output_json
        result = SemanticTaskResult(
            task_id="dreamer-verifier-task-1" if request.task_type == "verifier" else "dreamer-research-task-1",
            task_type=request.task_type,
            ok=True,
            status="succeeded",
            output_json=output_json,
            prompt_version=request.prompt_version,
            rubric_version=request.rubric_version,
            output_schema=request.output_schema,
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
            evidence_refs=list(request.evidence_refs or []),
            metadata=dict(request.metadata or {}),
        )
        row = record_semantic_task_run(str(request.root or ""), request, result)
        return replace(result, receipt_id=str(row.get("receipt_id") or ""))


class TestDreamerResearchSemanticTask(unittest.TestCase):
    def test_research_refines_existing_candidate_and_records_receipt(self):
        with tempfile.TemporaryDirectory(prefix="cm-dreamer-research-") as td:
            _write_candidates(
                td,
                [
                    {
                        "id": "dc-1",
                        "created_at": "2026-06-18T00:00:00+00:00",
                        "status": "pending",
                        "hypothesis_type": "goal_candidate",
                        "proposal_family": "goal",
                        "statement": "Repeated behavior suggests a goal.",
                        "supporting_bead_ids": ["b1", "b2"],
                    }
                ],
            )
            runtime = FakeDreamerResearchRuntime(
                {
                    "contract": "memory.dreamer_research.v1",
                    "run_id": "dream-run-1",
                    "candidate_refinements": [
                        {
                            "candidate_id": "dc-1",
                            "research_note": "Strong enough to review, but needs another future example.",
                            "suggested_review_priority": "high",
                            "confidence": 0.82,
                            "evidence_limitations": ["Only two supporting beads were provided."],
                            "falsifiability": "A future contrary decision would narrow the goal.",
                        },
                        {
                            "candidate_id": "missing",
                            "research_note": "Should be ignored because it is not in the queue.",
                        },
                    ],
                    "suggested_hypotheses": [{"statement": "Not enqueued in this conservative slice."}],
                }
            )

            with patch("core_memory.runtime.dreamer.research.get_semantic_task_runtime", return_value=runtime):
                out = run_dreamer_research(td, run_id="dream-run-1", source="unit")

            self.assertTrue(out.get("ok"), out)
            self.assertEqual("succeeded", out.get("status"))
            self.assertEqual(1, out.get("refined"))
            self.assertEqual(["missing"], out.get("unknown_candidate_ids"))
            self.assertEqual(1, out.get("suggested_hypotheses"))
            self.assertEqual(["dreamer_research", "verifier"], [r.task_type for r in runtime.requests])
            request = runtime.requests[0]
            self.assertEqual("dreamer_research", request.task_type)
            self.assertEqual("candidate_only", request.authority_boundary)
            self.assertEqual("deterministic_dreamer_candidates", request.fallback_mode)
            self.assertEqual(["dc-1"], (request.metadata or {}).get("candidate_ids"))

            candidates = _read_candidates(td)
            self.assertEqual(1, len(candidates))
            research = candidates[0].get("operator_research") or []
            self.assertEqual(1, len(research))
            self.assertEqual("high", research[0].get("suggested_review_priority"))
            self.assertIn("Strong enough", research[0].get("research_note"))
            self.assertTrue(research[0].get("receipt_id"))
            refs = candidates[0].get("semantic_task_refs") or []
            self.assertEqual("dreamer_research_refinement", refs[0].get("role"))

            receipts = list_semantic_task_runs(td, task_type="dreamer_research")
            self.assertEqual(1, receipts.get("count"))
            receipt = (receipts.get("results") or [{}])[0]
            self.assertEqual("frontier", receipt.get("model_tier"))
            self.assertEqual("candidate_only", receipt.get("authority_boundary"))
            verifier_receipts = list_semantic_task_runs(td, task_type="verifier")
            self.assertEqual(1, verifier_receipts.get("count"))
            self.assertEqual("cheap", (verifier_receipts.get("results") or [{}])[0].get("model_tier"))

    def test_verifier_block_prevents_dreamer_annotation(self):
        with tempfile.TemporaryDirectory(prefix="cm-dreamer-research-") as td:
            _write_candidates(
                td,
                [
                    {
                        "id": "dc-1",
                        "created_at": "2026-06-18T00:00:00+00:00",
                        "status": "pending",
                        "hypothesis_type": "goal_candidate",
                        "statement": "Repeated behavior suggests a goal.",
                    }
                ],
            )
            runtime = FakeDreamerResearchRuntime(
                {
                    "contract": "memory.dreamer_research.v1",
                    "candidate_refinements": [
                        {
                            "candidate_id": "dc-1",
                            "research_note": "This overreaches.",
                            "suggested_review_priority": "high",
                        }
                    ],
                },
                verifier_json={
                    "contract": "memory.semantic_task_verifier.v1",
                    "decision": "block",
                    "warnings": [],
                    "blocking_errors": ["unsupported inference"],
                },
            )

            with patch("core_memory.runtime.dreamer.research.get_semantic_task_runtime", return_value=runtime):
                out = run_dreamer_research(td, run_id="dream-run-blocked", source="unit")

            self.assertFalse(out.get("ok"))
            self.assertEqual("blocked_by_verifier", out.get("status"))
            self.assertEqual(0, out.get("refined"))
            self.assertNotIn("operator_research", _read_candidates(td)[0])
            verifier_receipts = list_semantic_task_runs(td, task_type="verifier")
            self.assertEqual(1, verifier_receipts.get("count"))

    def test_disabled_runtime_records_unavailable_without_candidate_mutation(self):
        with tempfile.TemporaryDirectory(prefix="cm-dreamer-research-") as td:
            _write_candidates(
                td,
                [
                    {
                        "id": "dc-1",
                        "created_at": "2026-06-18T00:00:00+00:00",
                        "status": "pending",
                        "hypothesis_type": "goal_candidate",
                        "statement": "A candidate exists.",
                    }
                ],
            )
            with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled"}, clear=False):
                out = run_dreamer_research(td, run_id="dream-run-disabled", source="unit")

            self.assertFalse(out.get("ok"))
            self.assertEqual("unavailable", out.get("status"))
            self.assertEqual(0, out.get("refined"))
            self.assertNotIn("operator_research", _read_candidates(td)[0])
            receipts = list_semantic_task_runs(td, task_type="dreamer_research", status="unavailable")
            self.assertEqual(1, receipts.get("count"))
            self.assertEqual(
                "semantic_task_runtime_disabled",
                (receipts.get("results") or [{}])[0].get("error"),
            )

    def test_dreamer_side_effect_runs_research_without_graph_mutation(self):
        with tempfile.TemporaryDirectory(prefix="cm-dreamer-sidefx-") as td:
            store = MemoryStore(td)
            b1 = store.add_bead(type="decision", title="A", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = store.add_bead(type="lesson", title="B", summary=["y"], session_id="s2", source_turn_ids=["t2"])
            with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled"}, clear=False), patch(
                "core_memory.runtime.queue.side_effect_queue.dreamer.run_analysis"
            ) as run_analysis:
                run_analysis.return_value = [
                    {
                        "source": b1,
                        "target": b2,
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.7,
                    }
                ]
                out = process_side_effect_event(root=td, kind="dreamer-run", payload={"mode": "suggest"})

            self.assertTrue(out.get("ok"), out)
            self.assertIn("dreamer_research", out)
            self.assertEqual("unavailable", (out.get("dreamer_research") or {}).get("status"))
            receipts = list_semantic_task_runs(td, task_type="dreamer_research", status="unavailable")
            self.assertEqual(1, receipts.get("count"))

            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([], idx.get("associations") or [])


if __name__ == "__main__":
    unittest.main()
