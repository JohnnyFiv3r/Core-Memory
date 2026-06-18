from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from core_memory.runtime.dreamer.candidates import _write_candidates
from core_memory.runtime.semantic_tasks import list_semantic_task_runs
from core_memory.runtime.semantic_tasks.contracts import SemanticTaskRequest, SemanticTaskResult
from core_memory.runtime.semantic_tasks.receipts import record_semantic_task_run
from core_memory.soul.dreamer_bridge import propose_soul_from_dreamer
from core_memory.soul.store import approve_soul_update, read_soul_file, soul_history


class FakeSoulProposalRuntime:
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
            task_id="soul-verifier-task-1" if request.task_type == "verifier" else "soul-proposal-task-1",
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


def _goal_candidate(candidate_id: str = "dc-goal-1") -> dict:
    return {
        "id": candidate_id,
        "status": "pending",
        "hypothesis_type": "goal_candidate",
        "goal_theme": "reduce-onboarding-friction",
        "statement": "Latent goal: reduce onboarding friction.",
        "supporting_bead_ids": ["b1", "b2", "b3"],
    }


class TestSoulProposalSemanticTask(unittest.TestCase):
    def test_soul_proposal_task_drafts_review_only_revision_with_receipt(self):
        with tempfile.TemporaryDirectory(prefix="cm-soul-proposal-") as td:
            _write_candidates(td, [_goal_candidate()])
            runtime = FakeSoulProposalRuntime(
                {
                    "contract": "memory.soul_proposal.v1",
                    "subject": "self",
                    "proposal_drafts": [
                        {
                            "candidate_id": "dc-goal-1",
                            "target_file": "GOALS.md",
                            "entry_key": "goal:reduce-onboarding-friction",
                            "content": "Consider reducing onboarding friction as a latent goal.",
                            "reason": "Dreamer found repeated supporting behavior.",
                            "review_notes": ["Confirm this is a durable goal, not a short-term task."],
                            "evidence_limitations": ["Only three supporting beads were provided."],
                        }
                    ],
                }
            )

            with patch("core_memory.soul.dreamer_bridge.get_semantic_task_runtime", return_value=runtime):
                out = propose_soul_from_dreamer(td)

            self.assertTrue(out.get("ok"), out)
            self.assertEqual(1, out.get("proposed"))
            self.assertEqual("succeeded", (out.get("soul_proposal") or {}).get("status"))
            self.assertEqual(1, (out.get("soul_proposal") or {}).get("drafted"))
            self.assertEqual(["soul_proposal", "verifier"], [r.task_type for r in runtime.requests])
            request = runtime.requests[0]
            self.assertEqual("soul_proposal", request.task_type)
            self.assertEqual("candidate_only", request.authority_boundary)
            self.assertEqual("deterministic_soul_bridge", request.fallback_mode)
            self.assertEqual(["dc-goal-1"], (request.metadata or {}).get("candidate_ids"))

            revisions = soul_history(td)["revisions"]
            self.assertEqual(1, len(revisions))
            revision = revisions[0]
            self.assertEqual("proposed", revision.get("status"))
            self.assertEqual("dreamer", revision.get("source"))
            self.assertEqual("inferred", revision.get("epistemic_status"))
            self.assertEqual("Consider reducing onboarding friction as a latent goal.", revision.get("content"))
            self.assertEqual("Dreamer found repeated supporting behavior.", revision.get("reason"))
            self.assertNotIn(
                "reducing onboarding friction",
                read_soul_file(td, file_name="GOALS.md")["markdown"],
            )

            refs = revision.get("semantic_task_refs") or []
            self.assertEqual("soul_proposal", refs[0].get("task_type"))
            self.assertEqual("soul_proposal_draft", refs[0].get("role"))
            self.assertTrue(refs[0].get("receipt_id"))
            self.assertEqual("verifier", refs[1].get("task_type"))
            self.assertEqual("semantic_task_verifier", refs[1].get("role"))
            metadata = revision.get("metadata") or {}
            self.assertTrue(metadata.get("used_operator_draft"))
            self.assertEqual(["Confirm this is a durable goal, not a short-term task."], metadata.get("operator_review_notes"))
            self.assertEqual("passed", (metadata.get("operator_verification") or {}).get("status"))

            receipts = list_semantic_task_runs(td, task_type="soul_proposal")
            self.assertEqual(1, receipts.get("count"))
            receipt = (receipts.get("results") or [{}])[0]
            self.assertEqual("frontier", receipt.get("model_tier"))
            self.assertEqual("candidate_only", receipt.get("authority_boundary"))
            verifier_receipts = list_semantic_task_runs(td, task_type="verifier")
            self.assertEqual(1, verifier_receipts.get("count"))
            self.assertEqual("cheap", (verifier_receipts.get("results") or [{}])[0].get("model_tier"))

            approved = approve_soul_update(td, revision_id=str(out["revision_ids"][0]), approver="human")
            self.assertTrue(approved.get("ok"), approved)
            self.assertIn(
                "Consider reducing onboarding friction",
                read_soul_file(td, file_name="GOALS.md")["markdown"],
            )

    def test_unavailable_soul_proposal_task_keeps_deterministic_proposal(self):
        with tempfile.TemporaryDirectory(prefix="cm-soul-proposal-") as td:
            _write_candidates(td, [_goal_candidate()])

            with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "disabled"}, clear=False):
                out = propose_soul_from_dreamer(td)

            self.assertTrue(out.get("ok"), out)
            self.assertEqual(1, out.get("proposed"))
            proposal = out.get("soul_proposal") or {}
            self.assertFalse(proposal.get("ok"))
            self.assertEqual("unavailable", proposal.get("status"))
            self.assertEqual("semantic_task_runtime_disabled", proposal.get("error"))

            revision = (soul_history(td)["revisions"] or [{}])[0]
            self.assertEqual("Latent goal: reduce onboarding friction.", revision.get("content"))
            self.assertFalse((revision.get("metadata") or {}).get("used_operator_draft"))
            refs = revision.get("semantic_task_refs") or []
            self.assertEqual("soul_proposal", refs[0].get("task_type"))
            self.assertTrue(refs[0].get("receipt_id"))

            receipts = list_semantic_task_runs(td, task_type="soul_proposal", status="unavailable")
            self.assertEqual(1, receipts.get("count"))
            self.assertEqual(
                "semantic_task_runtime_disabled",
                (receipts.get("results") or [{}])[0].get("error"),
            )

    def test_verifier_block_discards_operator_draft_and_uses_deterministic_content(self):
        with tempfile.TemporaryDirectory(prefix="cm-soul-proposal-") as td:
            _write_candidates(td, [_goal_candidate()])
            runtime = FakeSoulProposalRuntime(
                {
                    "contract": "memory.soul_proposal.v1",
                    "subject": "self",
                    "proposal_drafts": [
                        {
                            "candidate_id": "dc-goal-1",
                            "target_file": "GOALS.md",
                            "entry_key": "goal:reduce-onboarding-friction",
                            "content": "This draft should be discarded.",
                            "reason": "Verifier blocks it.",
                        }
                    ],
                },
                verifier_json={
                    "contract": "memory.semantic_task_verifier.v1",
                    "decision": "block",
                    "blocking_errors": ["unsupported endorsed claim"],
                    "warnings": [],
                },
            )

            with patch("core_memory.soul.dreamer_bridge.get_semantic_task_runtime", return_value=runtime):
                out = propose_soul_from_dreamer(td)

            self.assertTrue(out.get("ok"), out)
            self.assertEqual(1, out.get("proposed"))
            proposal = out.get("soul_proposal") or {}
            self.assertEqual("blocked_by_verifier", proposal.get("status"))
            self.assertEqual(0, proposal.get("drafted"))
            revision = (soul_history(td)["revisions"] or [{}])[0]
            self.assertEqual("Latent goal: reduce onboarding friction.", revision.get("content"))
            metadata = revision.get("metadata") or {}
            self.assertFalse(metadata.get("used_operator_draft"))
            self.assertEqual("blocked", (metadata.get("operator_verification") or {}).get("status"))
            refs = revision.get("semantic_task_refs") or []
            self.assertEqual(["soul_proposal", "verifier"], [r.get("task_type") for r in refs])


if __name__ == "__main__":
    unittest.main()
