from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from core_memory.policy.bead_typing import classify_bead_type, is_retrieval_turn
from core_memory.provider_config import ProviderConfig
from core_memory.runtime.semantic_tasks import SemanticTaskResult, list_semantic_task_runs
from core_memory.runtime.semantic_tasks.contracts import TASK_BEAD_TYPE_CLASSIFIER


class UnavailableRuntime:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return SemanticTaskResult(
            task_id="unavailable-classifier",
            task_type=request.task_type,
            ok=False,
            status="unavailable",
            error="missing_chat_provider",
            fallback_mode=request.fallback_mode,
            authority_boundary=request.authority_boundary,
        )


class TestBeadTypingSemanticTask(unittest.TestCase):
    def test_classifier_routes_through_semantic_task_runtime_and_records_receipt(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="fallback-model",
            source="unit",
            explicit=True,
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "provider",
                "CORE_MEMORY_AGENT_MODEL_CHEAP": "",
                "CORE_MEMORY_BEAD_TYPE_MODEL": "cheap-type-model",
                "CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "1",
            },
            clear=False,
        ), patch(
            "core_memory.policy.semantic_task_runtime.resolve_chat_config",
            return_value=cfg,
        ), patch(
            "core_memory.policy.semantic_task_runtime.chat_complete",
            return_value='{"type":"decision"}',
        ) as complete:
            out = classify_bead_type(
                "Record decision: use PostgreSQL over MySQL because JSONB support won.",
                "",
                root=td,
            )
            rows = list_semantic_task_runs(td, task_type=TASK_BEAD_TYPE_CLASSIFIER)

        self.assertEqual("decision", out)
        complete.assert_called_once()
        self.assertEqual("cheap-type-model", complete.call_args.kwargs["config"].model)
        self.assertEqual(1, rows.get("count"))
        row = (rows.get("results") or [{}])[0]
        self.assertEqual(TASK_BEAD_TYPE_CLASSIFIER, row.get("task_type"))
        self.assertEqual("succeeded", row.get("status"))
        self.assertEqual("cheap", row.get("model_tier"))
        self.assertEqual("bead_type_classifier.v1", row.get("prompt_version"))
        self.assertEqual("memory.bead_type_classifier.v1", row.get("output_schema"))
        self.assertEqual("heuristic_context", row.get("fallback_mode"))
        self.assertEqual("advisory", row.get("authority_boundary"))

    def test_retrieval_turns_are_context_without_runtime_call(self):
        text = "Can you remind me why PostgreSQL won over MySQL?"
        self.assertTrue(is_retrieval_turn(text))
        runtime_factory = Mock()
        with patch("core_memory.policy.bead_typing.get_semantic_task_runtime", runtime_factory):
            self.assertEqual("context", classify_bead_type(text, ""))
        runtime_factory.assert_not_called()

    def test_unavailable_runtime_falls_back_to_context_when_allowed(self):
        runtime = UnavailableRuntime()
        with patch.dict(
            os.environ,
            {"CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "1"},
            clear=False,
        ), patch("core_memory.policy.bead_typing.get_semantic_task_runtime", return_value=runtime):
            out = classify_bead_type("Record lesson: always benchmark real workloads.", "")

        self.assertEqual("context", out)
        self.assertEqual(1, len(runtime.requests))
        self.assertEqual(TASK_BEAD_TYPE_CLASSIFIER, runtime.requests[0].task_type)

    def test_unavailable_runtime_raises_when_fallback_disabled(self):
        with patch.dict(
            os.environ,
            {"CORE_MEMORY_BEAD_TYPE_ALLOW_FALLBACK": "0"},
            clear=False,
        ), patch("core_memory.policy.bead_typing.get_semantic_task_runtime", return_value=UnavailableRuntime()):
            with self.assertRaisesRegex(RuntimeError, "bead_type_llm_unavailable"):
                classify_bead_type("Record goal: ship the semantic runtime audit.", "")

    def test_policy_has_no_direct_provider_sdk_calls(self):
        here = os.path.dirname(__file__)
        path = os.path.abspath(os.path.join(here, "..", "core_memory", "policy", "bead_typing.py"))
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        self.assertNotIn("import anthropic", source)
        self.assertNotIn("from openai import OpenAI", source)
        self.assertNotIn("ANTHROPIC_API_KEY", source)
        self.assertNotIn("OPENAI_API_KEY", source)
        self.assertNotIn("_classify_anthropic", source)
        self.assertNotIn("_classify_openai", source)


if __name__ == "__main__":
    unittest.main()
