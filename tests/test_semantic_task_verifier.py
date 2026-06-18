from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

from core_memory.runtime.semantic_tasks import list_semantic_task_runs
from core_memory.runtime.semantic_tasks.contracts import SemanticTaskRequest, SemanticTaskResult
from core_memory.runtime.semantic_tasks.receipts import record_semantic_task_run
from core_memory.runtime.semantic_tasks.verifier import verify_semantic_task_output


class FakeVerifierRuntime:
    def __init__(self, output_json: dict | None = None, *, ok: bool = True, error: str = ""):
        self.output_json = output_json or {
            "contract": "memory.semantic_task_verifier.v1",
            "decision": "pass",
            "warnings": [],
            "blocking_errors": [],
        }
        self.ok = ok
        self.error = error
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        result = SemanticTaskResult(
            task_id="verifier-task-1",
            task_type=request.task_type,
            ok=self.ok,
            status="succeeded" if self.ok else "unavailable",
            output_json=self.output_json if self.ok else None,
            error="" if self.ok else self.error,
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


class TestSemanticTaskVerifier(unittest.TestCase):
    def test_deterministic_block_prevents_verifier_runtime_call(self):
        with tempfile.TemporaryDirectory(prefix="cm-verifier-") as td:
            runtime = FakeVerifierRuntime()
            out = verify_semantic_task_output(
                root=td,
                source_task_type="dreamer_research",
                source_task_id="task-1",
                output_schema="memory.dreamer_research.v1",
                output_json={"contract": "wrong.contract", "candidate_refinements": []},
                authority_boundary="candidate_only",
                evidence_refs=[{"type": "dreamer_candidate", "candidate_id": "dc-1"}],
                required_top_level_fields=["candidate_refinements"],
                runtime=runtime,
            )

            self.assertFalse(out.get("ok"))
            self.assertEqual("blocked", out.get("status"))
            self.assertIn("contract_mismatch", ",".join(out.get("blocking_errors") or []))
            self.assertEqual([], runtime.requests)

    def test_semantic_block_records_verifier_receipt(self):
        with tempfile.TemporaryDirectory(prefix="cm-verifier-") as td:
            runtime = FakeVerifierRuntime(
                {
                    "contract": "memory.semantic_task_verifier.v1",
                    "decision": "block",
                    "warnings": [],
                    "blocking_errors": ["unsupported inference"],
                }
            )
            out = verify_semantic_task_output(
                root=td,
                source_task_type="soul_proposal",
                source_task_id="task-1",
                output_schema="memory.soul_proposal.v1",
                output_json={"contract": "memory.soul_proposal.v1", "proposal_drafts": []},
                authority_boundary="candidate_only",
                evidence_refs=[{"type": "dreamer_candidate", "candidate_id": "dc-1"}],
                required_top_level_fields=["proposal_drafts"],
                runtime=runtime,
            )

            self.assertFalse(out.get("ok"))
            self.assertEqual("blocked", out.get("status"))
            self.assertEqual("block", out.get("decision"))
            self.assertEqual(1, len(runtime.requests))
            receipts = list_semantic_task_runs(td, task_type="verifier")
            self.assertEqual(1, receipts.get("count"))
            self.assertEqual("cheap", (receipts.get("results") or [{}])[0].get("model_tier"))

    def test_unavailable_verifier_can_be_required_or_warning_only(self):
        with tempfile.TemporaryDirectory(prefix="cm-verifier-") as td:
            runtime = FakeVerifierRuntime(ok=False, error="missing_chat_provider")
            optional = verify_semantic_task_output(
                root=td,
                source_task_type="dreamer_research",
                source_task_id="task-1",
                output_schema="memory.dreamer_research.v1",
                output_json={"contract": "memory.dreamer_research.v1", "candidate_refinements": []},
                authority_boundary="candidate_only",
                evidence_refs=[{"type": "dreamer_candidate", "candidate_id": "dc-1"}],
                required_top_level_fields=["candidate_refinements"],
                require_semantic_verifier=False,
                runtime=runtime,
            )
            required = verify_semantic_task_output(
                root=td,
                source_task_type="soul_proposal",
                source_task_id="task-2",
                output_schema="memory.soul_proposal.v1",
                output_json={"contract": "memory.soul_proposal.v1", "proposal_drafts": []},
                authority_boundary="candidate_only",
                evidence_refs=[{"type": "dreamer_candidate", "candidate_id": "dc-1"}],
                required_top_level_fields=["proposal_drafts"],
                require_semantic_verifier=True,
                runtime=runtime,
            )

            self.assertTrue(optional.get("ok"))
            self.assertEqual("unavailable", optional.get("status"))
            self.assertFalse(required.get("ok"))
            self.assertEqual("blocked", required.get("status"))
            self.assertIn("semantic_verifier_unavailable", required.get("blocking_errors") or [])


if __name__ == "__main__":
    unittest.main()
