from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.provider_config import ProviderConfig
from core_memory.runtime.semantic_tasks import (
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
    get_semantic_task_runtime,
    list_semantic_task_runs,
    record_semantic_task_run,
    resolve_model_profile,
    summarize_semantic_task_runs,
    task_profile,
)
from core_memory.runtime.semantic_tasks.contracts import (
    TASK_ASSOCIATION_DECISION,
    TASK_BEAD_FIELD_JUDGE,
    TASK_SOUL_PROPOSAL,
    TASK_VERIFIER,
)
from core_memory.runtime.semantic_tasks.runtime import DisabledSemanticTaskRuntime, ProviderSemanticTaskRuntime


class TestSemanticTaskRuntimeFoundation(unittest.TestCase):
    def test_task_profile_uses_expected_model_tiers(self):
        self.assertEqual("cheap", task_profile("bead_field_judge").model_tier)
        self.assertEqual("standard", task_profile("association_decision").model_tier)
        self.assertEqual("frontier", task_profile("dreamer_research").model_tier)
        self.assertEqual("candidate_only", task_profile("dreamer_research").authority_boundary)
        self.assertEqual("frontier", task_profile(TASK_SOUL_PROPOSAL).model_tier)
        self.assertEqual("candidate_only", task_profile(TASK_SOUL_PROPOSAL).authority_boundary)
        self.assertEqual("cheap", task_profile(TASK_VERIFIER).model_tier)
        self.assertEqual("advisory", task_profile(TASK_VERIFIER).authority_boundary)

    def test_model_profile_uses_agent_tier_env_aliases(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="fallback-model",
            source="unit",
            explicit=True,
        )
        with patch.dict(os.environ, {"CORE_MEMORY_AGENT_MODEL_CHEAP": "cheap-model"}, clear=False):
            profile = resolve_model_profile(TASK_BEAD_FIELD_JUDGE, config=cfg)
        self.assertEqual("cheap", profile.tier)
        self.assertEqual("cheap-model", profile.model)
        self.assertEqual("CORE_MEMORY_AGENT_MODEL_CHEAP", profile.source)

    def test_disabled_runtime_writes_unavailable_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            result = DisabledSemanticTaskRuntime().run(
                SemanticTaskRequest(
                    root=td,
                    task_type=TASK_BEAD_FIELD_JUDGE,
                    prompt="Judge this.",
                    fallback_mode="heuristic",
                    prompt_version="bead_field_judge.v1",
                )
            )
            self.assertFalse(result.ok)
            self.assertEqual("unavailable", result.status)
            self.assertTrue(result.receipt_id)

            rows = list_semantic_task_runs(td)
            self.assertEqual(1, rows.get("count"))
            row = (rows.get("results") or [{}])[0]
            self.assertEqual("memory.semantic_task_runs.v1", row.get("contract"))
            self.assertEqual(TASK_BEAD_FIELD_JUDGE, row.get("task_type"))
            self.assertEqual("heuristic", row.get("fallback_mode"))
            self.assertTrue(row.get("input_hash"))
            self.assertTrue(row.get("output_hash"))

    def test_provider_runtime_success_writes_receipt_and_json(self):
        cfg = ProviderConfig(
            kind="chat",
            provider="openai",
            base_url="https://example.test/v1",
            api_key="test",
            model="gpt-test",
            source="unit",
            explicit=True,
        )
        with tempfile.TemporaryDirectory() as td:
            with (
                patch("core_memory.runtime.semantic_tasks.runtime.resolve_chat_config", return_value=cfg),
                patch(
                    "core_memory.runtime.semantic_tasks.runtime.chat_complete",
                    return_value='{"decision":"accept","reason_text":"Supported by evidence."}',
                ) as complete,
            ):
                result = ProviderSemanticTaskRuntime().run(
                    SemanticTaskRequest(
                        root=td,
                        task_type=TASK_ASSOCIATION_DECISION,
                        prompt="Return JSON.",
                        json_mode=True,
                        prompt_version="association_judge.v1",
                        rubric_version="association_truth.v1",
                        output_schema="memory.association_judge.v1",
                        metadata={"run_id": "assoc-run-1"},
                    )
                )

            self.assertTrue(result.ok)
            self.assertEqual("succeeded", result.status)
            self.assertEqual({"decision": "accept", "reason_text": "Supported by evidence."}, result.output_json)
            complete.assert_called_once()

            rows = list_semantic_task_runs(td, task_type=TASK_ASSOCIATION_DECISION)
            self.assertEqual(1, rows.get("count"))
            row = (rows.get("results") or [{}])[0]
            self.assertEqual("standard", row.get("model_tier"))
            self.assertEqual("gpt-test", (row.get("model_profile") or {}).get("model"))
            self.assertEqual("association_judge.v1", row.get("prompt_version"))
            self.assertEqual("association_truth.v1", row.get("rubric_version"))
            self.assertEqual("assoc-run-1", (row.get("metadata") or {}).get("run_id"))

    def test_record_and_list_filters_are_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            req_a = SemanticTaskRequest(root=td, task_type=TASK_BEAD_FIELD_JUDGE, task_id="a")
            req_b = SemanticTaskRequest(root=td, task_type=TASK_ASSOCIATION_DECISION, task_id="b")
            result_a = DisabledSemanticTaskRuntime().run(req_a)
            result_b = DisabledSemanticTaskRuntime().run(req_b)
            self.assertTrue(result_a.receipt_id)
            self.assertTrue(result_b.receipt_id)

            filtered = list_semantic_task_runs(td, task_type=TASK_ASSOCIATION_DECISION, status="unavailable")
            self.assertEqual(1, filtered.get("count"))
            self.assertEqual("b", (filtered.get("results") or [{}])[0].get("task_id"))

            all_rows = list_semantic_task_runs(td)
            self.assertEqual(["b", "a"], [row.get("task_id") for row in all_rows.get("results") or []])

    def test_summarize_semantic_task_runs_counts_activity_and_attention(self):
        with tempfile.TemporaryDirectory() as td:
            DisabledSemanticTaskRuntime().run(
                SemanticTaskRequest(
                    root=td,
                    task_type=TASK_BEAD_FIELD_JUDGE,
                    task_id="judge-unavailable",
                    fallback_mode="heuristic",
                    metadata={"runtime_mode": "disabled"},
                )
            )
            record_semantic_task_run(
                td,
                SemanticTaskRequest(root=td, task_type=TASK_ASSOCIATION_DECISION, task_id="assoc-ok"),
                SemanticTaskResult(
                    task_id="assoc-ok",
                    task_type=TASK_ASSOCIATION_DECISION,
                    ok=True,
                    status="succeeded",
                    model_profile=ModelProfile(tier="standard", model="standard-test", runtime="provider"),
                    latency_ms=40,
                    token_usage={"input_tokens": 10, "output_tokens": 5},
                    metadata={"runtime_mode": "provider"},
                ),
            )
            record_semantic_task_run(
                td,
                SemanticTaskRequest(root=td, task_type=TASK_VERIFIER, task_id="verifier-failed"),
                SemanticTaskResult(
                    task_id="verifier-failed",
                    task_type=TASK_VERIFIER,
                    ok=False,
                    status="failed",
                    model_profile=ModelProfile(tier="cheap", model="cheap-test", runtime="provider"),
                    latency_ms=20,
                    token_usage={"input_tokens": 7},
                    error="model_timeout",
                    metadata={"runtime_mode": "provider", "source_task_type": TASK_ASSOCIATION_DECISION},
                ),
            )

            summary = summarize_semantic_task_runs(td, limit=2)

            self.assertTrue(summary.get("ok"))
            self.assertEqual("memory.semantic_task_summary.v1", summary.get("contract"))
            self.assertEqual(3, summary.get("total_runs"))
            self.assertEqual("verifier-failed", (summary.get("latest_run") or {}).get("task_id"))
            counts = summary.get("counts") or {}
            self.assertEqual(1, (counts.get("by_task_type") or {}).get(TASK_ASSOCIATION_DECISION))
            self.assertEqual(1, (counts.get("by_status") or {}).get("failed"))
            self.assertEqual(2, (counts.get("by_model_tier") or {}).get("cheap"))
            self.assertEqual(2, (counts.get("by_runtime_mode") or {}).get("provider"))
            self.assertEqual(2, (summary.get("attention") or {}).get("count"))
            self.assertEqual("verifier-failed", ((summary.get("attention") or {}).get("recent") or [{}])[0].get("task_id"))
            self.assertEqual(2, (summary.get("errors") or {}).get("count"))
            self.assertEqual(1, ((summary.get("errors") or {}).get("by_error") or {}).get("model_timeout"))
            self.assertEqual(17, ((summary.get("token_usage") or {}).get("total") or {}).get("input_tokens"))
            self.assertEqual(30, (summary.get("latency_ms") or {}).get("avg"))

    def test_http_semantic_task_runs_endpoint(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            req = SemanticTaskRequest(root=td, task_type=TASK_BEAD_FIELD_JUDGE, task_id="http-task")
            DisabledSemanticTaskRuntime().run(req)

            client = TestClient(app)
            response = client.get(
                "/v1/memory/semantic-task-runs",
                params={"root": td, "task_type": TASK_BEAD_FIELD_JUDGE, "status": "unavailable", "limit": 10},
            )
            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual("memory.semantic_task_runs.v1", data.get("contract"))
            self.assertGreaterEqual(data.get("count"), 1)
            self.assertEqual(TASK_BEAD_FIELD_JUDGE, (data.get("results") or [{}])[0].get("task_type"))

    def test_http_semantic_task_runs_summary_endpoint(self):
        try:
            from fastapi.testclient import TestClient
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            DisabledSemanticTaskRuntime().run(
                SemanticTaskRequest(root=td, task_type=TASK_BEAD_FIELD_JUDGE, task_id="http-summary-task")
            )

            client = TestClient(app)
            response = client.get(
                "/v1/memory/semantic-task-runs/summary",
                params={"root": td, "limit": 5},
            )
            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual("memory.semantic_task_summary.v1", data.get("contract"))
            self.assertEqual(1, data.get("total_runs"))
            self.assertEqual("http-summary-task", (data.get("latest_run") or {}).get("task_id"))

    def test_factory_returns_provider_runtime_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            runtime = get_semantic_task_runtime()
        self.assertIsInstance(runtime, ProviderSemanticTaskRuntime)


if __name__ == "__main__":
    unittest.main()
