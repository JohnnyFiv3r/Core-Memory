from __future__ import annotations

from unittest.mock import patch

from core_memory.policy.bead_judge import judge_bead_fields
from core_memory.policy.turn_memory_authoring import author_turn_memory, repair_turn_memory
from core_memory.schema.agent_authored_updates import AGENT_AUTHORED_UPDATES_V1
from core_memory.schema.semantic_tasks import (
    TASK_TURN_MEMORY_AUTHORING,
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
)


class FakeSemanticTaskRuntime:
    def __init__(self, output: dict):
        self.output = output
        self.requests: list[SemanticTaskRequest] = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        return SemanticTaskResult(
            task_id="task-1",
            task_type=request.task_type,
            ok=True,
            status="succeeded",
            output_json=self.output,
            model_profile=ModelProfile(
                tier="standard",
                provider="openai",
                model="gpt-test",
                runtime="provider",
            ),
            prompt_version=request.prompt_version,
            rubric_version=request.rubric_version,
            output_schema=request.output_schema,
            input_hash="input-hash",
            output_hash="output-hash",
            authority_boundary=request.authority_boundary,
            receipt_id="receipt-1",
        )


def _delegated_output() -> dict:
    return {
        "schema_version": AGENT_AUTHORED_UPDATES_V1,
        "beads_create": [
            {
                "creation_role": "current_turn",
                "type": "decision",
                "title": "Delegated author produced a full bead",
                "summary": ["The delegated semantic agent used the full schema."],
                "entities": ["Core Memory"],
                "retrieval_eligible": True,
                "retrieval_title": "Core Memory delegated semantic author",
                "retrieval_facts": ["Delegated authoring returns agent_authored_updates.v1."],
                "because": ["The passive bridge explicitly requested delegated authorship."],
                "source_turn_ids": ["t1"],
                "decision_keys": ["delegated-semantic-author"],
            }
        ],
        "associations": [],
        "reviewed_beads": [],
    }


def test_delegated_author_uses_full_schema_and_records_provenance() -> None:
    runtime = FakeSemanticTaskRuntime(_delegated_output())
    updates, diag = author_turn_memory(
        root="/tmp/core-memory-test",
        req={
            "session_id": "s1",
            "turn_id": "t1",
            "turns": [
                {"speaker": "user", "role": "user", "content": "Remember this decision."},
                {"speaker": "assistant", "role": "assistant", "content": "Recorded."},
            ],
            "speakers": ["user", "assistant"],
            "tools_trace": [],
            "mesh_trace": [],
            "window_turn_ids": [],
            "window_bead_ids": [],
        },
        crawler_context={"session_id": "s1", "visible_bead_ids": [], "beads": []},
        task_runtime=runtime,
    )

    assert updates == _delegated_output()
    assert diag["ok"] is True
    assert diag["source"] == "delegated_semantic_agent"
    request = runtime.requests[0]
    assert request.task_type == TASK_TURN_MEMORY_AUTHORING
    assert request.output_schema == AGENT_AUTHORED_UPDATES_V1
    assert request.authority_boundary == "semantic_author"
    assert request.fallback_mode == "none"
    assert AGENT_AUTHORED_UPDATES_V1 in request.prompt

    authorship = diag["authorship"]
    assert authorship["source"] == "delegated_semantic_agent"
    assert authorship["model_profile"]["model"] == "gpt-test"
    assert authorship["schema_version"] == AGENT_AUTHORED_UPDATES_V1
    assert authorship["task_receipt_id"] == "receipt-1"
    assert authorship["grounding_hash"]
    assert authorship["validation"] == {"ok": True, "errors": []}


def test_delegated_author_rejects_narrow_legacy_judge_output() -> None:
    runtime = FakeSemanticTaskRuntime(
        {
            "type": "decision",
            "title": "Narrow output",
            "summary": ["Missing the authored-update envelope."],
        }
    )
    updates, diag = author_turn_memory(
        root="/tmp/core-memory-test",
        req={"session_id": "s1", "turn_id": "t1", "turns": [], "speakers": []},
        crawler_context={"session_id": "s1", "visible_bead_ids": [], "beads": []},
        task_runtime=runtime,
    )

    assert updates is None
    assert diag["ok"] is False
    assert diag["error_code"] == "delegated_semantic_author_invalid"
    assert diag["authorship"]["validation"]["ok"] is False


def test_explicit_repair_uses_same_task_and_attributes_changed_fields() -> None:
    runtime = FakeSemanticTaskRuntime(_delegated_output())
    invalid = _delegated_output()
    invalid["beads_create"][0].pop("because")
    updates, diag = repair_turn_memory(
        root="/tmp/core-memory-test",
        req={
            "session_id": "s1",
            "turn_id": "t1",
            "turns": [],
            "speakers": [],
            "authorship_provenance": {"source": "primary_agent"},
        },
        crawler_context={"session_id": "s1", "visible_bead_ids": [], "beads": []},
        invalid_updates=invalid,
        validation={"error_code": "agent_causal_rationale_missing"},
        task_runtime=runtime,
    )

    assert updates == _delegated_output()
    assert runtime.requests[0].task_type == TASK_TURN_MEMORY_AUTHORING
    assert runtime.requests[0].authority_boundary == "semantic_repair_agent"
    assert runtime.requests[0].metadata["authoring_operation"] == "repair"
    assert "EXPLICIT REPAIR MODE" in runtime.requests[0].prompt
    authorship = diag["authorship"]
    assert authorship["source"] == "repair_agent"
    assert authorship["repair_used"] is True
    assert authorship["repaired_fields"] == ["$.beads_create[0].because[0]"]
    assert authorship["primary_authorship"] == {"source": "primary_agent"}
    assert authorship["field_provenance"]["$.beads_create[0].because[0]"]["task_receipt_id"] == "receipt-1"


def test_bead_judge_task_name_is_a_compatibility_alias_for_full_v1_output() -> None:
    runtime = FakeSemanticTaskRuntime(_delegated_output())
    with patch("core_memory.policy.bead_judge.get_semantic_task_runtime", return_value=runtime):
        judged = judge_bead_fields("Delegate this turn.", "Recorded.", mode="llm")

    assert judged["title"] == "Delegated author produced a full bead"
    assert judged["retrieval_title"] == "Core Memory delegated semantic author"
    assert judged["retrieval_facts"] == ["Delegated authoring returns agent_authored_updates.v1."]
    assert judged["decision_keys"] == ["delegated-semantic-author"]
    request = runtime.requests[0]
    assert request.task_type == "bead_field_judge"
    assert request.output_schema == AGENT_AUTHORED_UPDATES_V1
    assert request.prompt_version == "turn_memory_authoring.v1"
    assert request.metadata["compatibility_alias_for"] == "turn_memory_authoring"
