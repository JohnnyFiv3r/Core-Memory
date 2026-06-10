"""HTTP /v1/memory/recall parity with the MCP recall tool.

Both wire surfaces share core_memory.integrations.recall_payload, so they must
return the same RecallResult contract for the same store and query.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestHttpRecallEndpoint(unittest.TestCase):
    def setUp(self):
        self._old_semantic_mode = os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE")
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def tearDown(self):
        if getattr(self, "_old_semantic_mode", None) is None:
            os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        else:
            os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = self._old_semantic_mode

    def _client(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        return TestClient(app)

    def test_recall_returns_recall_result_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post("/v1/memory/recall", json={"root": root, "query": "anything", "effort": "low"})
            self.assertEqual(200, r.status_code)
            body = r.json()
            # RecallResult contract keys must be present; empty store must not 500.
            self.assertTrue(set(body) >= {"ok", "status", "evidence"})
            self.assertIn("tier_path", body)
            self.assertIn("steps", body)

    def test_recall_missing_query_is_400(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post("/v1/memory/recall", json={"root": root, "query": ""})
            self.assertEqual(400, r.status_code)
            body = r.json()
            self.assertEqual("cm.invalid_request", body["error"]["code"])
            self.assertEqual("query", body["error"]["data"]["field"])

    def test_recall_invalid_effort_is_400(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            for effort in ("bogus", "dynamic"):
                r = c.post("/v1/memory/recall", json={"root": root, "query": "q", "effort": effort})
                self.assertEqual(400, r.status_code, f"effort={effort}")
                body = r.json()
                self.assertEqual("cm.invalid_request", body["error"]["code"])
                self.assertEqual("effort", body["error"]["data"]["field"])

    def test_recall_parity_with_mcp_handler(self):
        from core_memory.integrations.mcp.tools.recall import recall_handler

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            query = "what changed in the deploy"
            c = self._client()
            http_body = c.post(
                "/v1/memory/recall",
                json={"root": root, "query": query, "effort": "medium", "include_raw": False},
            ).json()
            mcp_body = recall_handler({"root": root, "query": query, "effort": "medium", "include_raw": False})
            self.assertEqual(set(mcp_body.keys()), set(http_body.keys()))
            self.assertEqual(mcp_body.get("ok"), http_body.get("ok"))
            self.assertEqual(mcp_body.get("status"), http_body.get("status"))

    def test_recall_speaker_list_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post(
                "/v1/memory/recall",
                json={"root": root, "query": "q", "effort": "low", "speaker": ["alice", "bob"]},
            )
            self.assertEqual(200, r.status_code)
