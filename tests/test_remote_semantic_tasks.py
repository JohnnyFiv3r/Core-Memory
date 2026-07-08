from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.integrations.remote.semantic_tasks import RemoteSemanticTaskRuntime
from core_memory.persistence.semantic_task_receipts import list_semantic_task_runs
from core_memory.policy.semantic_task_runtime import get_semantic_task_runtime, semantic_task_runtime_mode
from core_memory.schema.semantic_tasks import (
    ModelProfile,
    SemanticTaskRequest,
    SemanticTaskResult,
    TASK_ASSOCIATION_DECISION,
)


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _FallbackRuntime:
    def __init__(self):
        self.requests = []

    def run(self, request: SemanticTaskRequest) -> SemanticTaskResult:
        self.requests.append(request)
        return SemanticTaskResult(
            task_id=request.task_id or "fallback-task",
            task_type=request.task_type,
            ok=True,
            status="succeeded",
            output_text="fallback",
            model_profile=ModelProfile(
                tier="standard",
                provider="openai",
                adapter="provider",
                model="fallback-model",
                runtime="provider",
            ),
            fallback_mode=request.fallback_mode,
            metadata={"runtime_mode": "provider"},
        )


class TestRemoteSemanticTaskRuntime(unittest.TestCase):
    def test_runtime_mode_accepts_remote_and_delegated_alias(self):
        with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "remote"}, clear=True):
            self.assertEqual("remote", semantic_task_runtime_mode())
        with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "delegated"}, clear=True):
            self.assertEqual("remote", semantic_task_runtime_mode())

    def test_factory_returns_remote_runtime_when_opted_in(self):
        with patch.dict(os.environ, {"CORE_MEMORY_SEMANTIC_TASK_RUNTIME": "remote"}, clear=True):
            runtime = get_semantic_task_runtime()

        self.assertIsInstance(runtime, RemoteSemanticTaskRuntime)

    def test_remote_runtime_posts_request_and_records_remote_receipt(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                {
                    "task_id": "remote-task",
                    "task_type": TASK_ASSOCIATION_DECISION,
                    "ok": True,
                    "status": "succeeded",
                    "output_text": '{"decision":"accept"}',
                    "output_json": {"decision": "accept"},
                    "model_profile": {
                        "tier": "standard",
                        "provider": "openai",
                        "adapter": "pydanticai",
                        "model": "gpt-standard",
                        "runtime": "remote",
                        "source": "AGENT_MODEL_STANDARD",
                    },
                    "input_hash": "input-hash",
                    "output_hash": "output-hash",
                    "latency_ms": 25,
                    "token_usage": {"prompt_tokens": 10, "completion_tokens": 4},
                    "receipt_id": "conductor-semrun-1",
                    "metadata": {"runtime_mode": "remote"},
                }
            )

        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.integrations.remote.semantic_tasks.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = RemoteSemanticTaskRuntime(
                url="https://agent.test/v1/semantic-task",
                token="secret",
                timeout_seconds=9,
            ).run(
                SemanticTaskRequest(
                    root=td,
                    task_id="remote-task",
                    task_type=TASK_ASSOCIATION_DECISION,
                    prompt="Return JSON.",
                    model_tier="standard",
                )
            )
            rows = list_semantic_task_runs(td)

        self.assertTrue(result.ok)
        self.assertTrue(result.receipt_id)
        self.assertEqual("https://agent.test/v1/semantic-task", captured["url"])
        self.assertEqual(9, captured["timeout"])
        self.assertEqual("Bearer secret", captured["headers"].get("Authorization"))
        self.assertNotIn("root", captured["body"])
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("remote", (row.get("metadata") or {}).get("runtime_mode"))
        self.assertEqual("remote", (row.get("model_profile") or {}).get("runtime"))
        self.assertEqual(
            "conductor-semrun-1",
            (row.get("result_refs") or {}).get("conductor_receipt_id"),
        )

    def test_remote_runtime_strict_returns_unavailable_without_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            result = RemoteSemanticTaskRuntime(
                url="",
                token="",
                strict=True,
            ).run(
                SemanticTaskRequest(
                    root=td,
                    task_id="strict-task",
                    task_type=TASK_ASSOCIATION_DECISION,
                    prompt="Return JSON.",
                )
            )
            rows = list_semantic_task_runs(td)

        self.assertFalse(result.ok)
        self.assertEqual("unavailable", result.status)
        self.assertEqual("remote_runtime_not_configured", result.error)
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("remote", (row.get("metadata") or {}).get("runtime_mode"))

    def test_remote_runtime_falls_back_to_provider_and_records_remote_fallback(self):
        fallback = _FallbackRuntime()
        with tempfile.TemporaryDirectory() as td:
            result = RemoteSemanticTaskRuntime(
                url="",
                token="",
                strict=False,
                fallback_runtime=fallback,
            ).run(
                SemanticTaskRequest(
                    root=td,
                    task_id="fallback-task",
                    task_type=TASK_ASSOCIATION_DECISION,
                    prompt="Return JSON.",
                )
            )
            rows = list_semantic_task_runs(td)

        self.assertTrue(result.ok)
        self.assertEqual("provider", result.fallback_mode)
        self.assertEqual(1, len(fallback.requests))
        self.assertIsNone(fallback.requests[0].root)
        row = (rows.get("results") or [{}])[0]
        self.assertEqual("remote_fallback", (row.get("metadata") or {}).get("runtime_mode"))
        self.assertEqual("remote_fallback", (row.get("model_profile") or {}).get("runtime"))
        self.assertEqual(
            "remote_runtime_not_configured",
            (row.get("metadata") or {}).get("remote_error"),
        )


if __name__ == "__main__":
    unittest.main()
