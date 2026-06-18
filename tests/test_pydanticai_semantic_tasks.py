from __future__ import annotations

import importlib.util
import tempfile
import unittest
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.pydanticai

if importlib.util.find_spec("pydantic_ai") is None:
    pytest.skip("pydantic-ai extra not installed", allow_module_level=True)

from core_memory.integrations.pydanticai.semantic_tasks import PydanticAISemanticTaskRuntime
from core_memory.runtime.semantic_tasks import SemanticTaskRequest, get_semantic_task_runtime, list_semantic_task_runs
from core_memory.runtime.semantic_tasks.contracts import (
    TASK_ASSOCIATION_DECISION,
    TASK_BEAD_FIELD_JUDGE,
    TASK_DREAMER_RESEARCH,
)


class FakeUsage:
    input_tokens = 11
    output_tokens = 7


class FakeRunResult:
    def __init__(self, output: str):
        self.output = output

    def usage(self):
        return FakeUsage()


class FakeAgent:
    instances = []

    def __init__(self, model, *, output_type=str, instructions="", **kwargs):
        self.model = model
        self.output_type = output_type
        self.instructions = instructions
        self.kwargs = kwargs
        self.calls = []
        FakeAgent.instances.append(self)

    def run_sync(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return FakeRunResult('{"decision":"accept","reason_text":"grounded"}')


class TestPydanticAISemanticTaskRuntime(unittest.TestCase):
    def setUp(self):
        FakeAgent.instances = []

    def test_pydanticai_runtime_executes_task_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_MODEL_STANDARD": "openai:gpt-standard",
                "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "pydanticai",
            },
            clear=False,
        ), patch(
            "core_memory.integrations.pydanticai.semantic_tasks._agent_class",
            return_value=FakeAgent,
        ):
            result = PydanticAISemanticTaskRuntime().run(
                SemanticTaskRequest(
                    root=td,
                    task_type=TASK_ASSOCIATION_DECISION,
                    prompt="Return JSON.",
                    json_mode=True,
                    prompt_version="association_judge.v1",
                    rubric_version="association_truth.v1",
                    output_schema="memory.association_judge.v1",
                    fallback_mode="pending_judge",
                    authority_boundary="advisory",
                    metadata={"run_id": "assoc-run-1"},
                )
            )
            rows = list_semantic_task_runs(td, task_type=TASK_ASSOCIATION_DECISION)

        self.assertTrue(result.ok)
        self.assertEqual("succeeded", result.status)
        self.assertEqual({"decision": "accept", "reason_text": "grounded"}, result.output_json)
        self.assertEqual("openai:gpt-standard", FakeAgent.instances[0].model)
        self.assertEqual("Return JSON.", FakeAgent.instances[0].calls[0]["prompt"])
        self.assertEqual(1, rows.get("count"))
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("pydanticai", (row.get("model_profile") or {}).get("runtime"))
        self.assertEqual("standard", row.get("model_tier"))
        self.assertEqual("association_judge.v1", row.get("prompt_version"))
        self.assertEqual("association_truth.v1", row.get("rubric_version"))
        self.assertEqual("pending_judge", row.get("fallback_mode"))
        self.assertEqual("assoc-run-1", (row.get("metadata") or {}).get("run_id"))

    def test_runtime_factory_returns_pydanticai_runtime_when_opted_in(self):
        with patch.dict("os.environ", {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "pydanticai"}, clear=False):
            runtime = get_semantic_task_runtime()
        self.assertIsInstance(runtime, PydanticAISemanticTaskRuntime)

    def test_pydanticai_runtime_routes_task_model_tiers(self):
        with patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_AGENT_MODEL_CHEAP": "openai:gpt-cheap",
                "CORE_MEMORY_AGENT_MODEL_FRONTIER": "anthropic:claude-frontier",
            },
            clear=False,
        ), patch(
            "core_memory.integrations.pydanticai.semantic_tasks._agent_class",
            return_value=FakeAgent,
        ):
            cheap = PydanticAISemanticTaskRuntime().run(
                SemanticTaskRequest(task_type=TASK_BEAD_FIELD_JUDGE, prompt="cheap", json_mode=True)
            )
            frontier = PydanticAISemanticTaskRuntime().run(
                SemanticTaskRequest(task_type=TASK_DREAMER_RESEARCH, prompt="frontier", json_mode=True)
            )

        self.assertTrue(cheap.ok)
        self.assertTrue(frontier.ok)
        self.assertEqual("openai:gpt-cheap", FakeAgent.instances[0].model)
        self.assertEqual("anthropic:claude-frontier", FakeAgent.instances[1].model)
        self.assertEqual("cheap", (cheap.model_profile or {}).tier)
        self.assertEqual("frontier", (frontier.model_profile or {}).tier)

    def test_pydanticai_runtime_unavailable_records_receipt(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.integrations.pydanticai.semantic_tasks._agent_class",
            side_effect=ImportError("missing"),
        ):
            result = PydanticAISemanticTaskRuntime().run(
                SemanticTaskRequest(
                    root=td,
                    task_type=TASK_BEAD_FIELD_JUDGE,
                    prompt="judge",
                    fallback_mode="heuristic",
                )
            )
            rows = list_semantic_task_runs(td, task_type=TASK_BEAD_FIELD_JUDGE, status="unavailable")

        self.assertFalse(result.ok)
        self.assertEqual("unavailable", result.status)
        self.assertEqual("pydanticai_semantic_task_runtime_unavailable", result.error)
        self.assertTrue(result.receipt_id)
        self.assertEqual(1, rows.get("count"))
        self.assertEqual("heuristic", (rows.get("results") or [{}])[0].get("fallback_mode"))


if __name__ == "__main__":
    unittest.main()
