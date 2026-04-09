from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestHttpAsyncJobsSlice52A(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_status_endpoint_returns_schema_tag(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.get("/v1/ops/async-jobs/status", params={"root": root})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", data.get("schema_version"))
            self.assertIn("queues", data)

    def test_enqueue_then_run_semantic_queue(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)

            enq = c.post(
                "/v1/ops/async-jobs/enqueue",
                json={"root": root, "kind": "semantic-rebuild"},
            )
            self.assertEqual(200, enq.status_code)
            enq_data = enq.json()
            self.assertTrue(enq_data.get("ok"))

            run = c.post(
                "/v1/ops/async-jobs/run",
                json={"root": root, "run_semantic": True, "max_compaction": 0},
            )
            self.assertEqual(200, run.status_code)
            run_data = run.json()
            self.assertIn("semantic_run", run_data)
            self.assertIn("side_effect_run", run_data)
            self.assertIn("status_after", run_data)
            self.assertEqual("core_memory.async_jobs.v1", run_data.get("schema_version"))

    def test_tenant_scopes_compaction_enqueue(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app, _resolve_root

        with tempfile.TemporaryDirectory() as td:
            base_root = str(Path(td) / "memory")
            tenant = "tenant-a"
            c = TestClient(app)

            r = c.post(
                "/v1/ops/async-jobs/enqueue",
                json={
                    "root": base_root,
                    "kind": "compaction",
                    "event": {"runId": "r1"},
                    "ctx": {"sessionId": "s1"},
                },
                headers={"X-Tenant-Id": tenant},
            )
            self.assertEqual(200, r.status_code)
            self.assertTrue((r.json() or {}).get("ok"))

            tenant_q = Path(_resolve_root(base_root, tenant)) / ".beads" / "events" / "compaction-queue.json"
            default_q = Path(base_root) / ".beads" / "events" / "compaction-queue.json"
            self.assertTrue(tenant_q.exists())
            self.assertFalse(default_q.exists())
            rows = json.loads(tenant_q.read_text(encoding="utf-8"))
            self.assertEqual(1, len(rows))

    def test_auth_applies_to_async_ops_endpoints(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http import server as srv

        old = os.environ.get("CORE_MEMORY_HTTP_TOKEN")
        os.environ["CORE_MEMORY_HTTP_TOKEN"] = "secret"
        try:
            c = TestClient(srv.app)
            r1 = c.get("/v1/ops/async-jobs/status")
            self.assertEqual(401, r1.status_code)

            r2 = c.get("/v1/ops/async-jobs/status", headers={"Authorization": "Bearer secret"})
            self.assertEqual(200, r2.status_code)
            self.assertTrue((r2.json() or {}).get("ok"))
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_HTTP_TOKEN", None)
            else:
                os.environ["CORE_MEMORY_HTTP_TOKEN"] = old

    def test_enqueue_unknown_kind_returns_400_structured_error(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/ops/async-jobs/enqueue",
                json={"root": root, "kind": "does-not-exist"},
            )
            self.assertEqual(400, r.status_code)
            data = r.json()
            self.assertFalse(data.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", data.get("schema_version"))
            err = data.get("error") or {}
            self.assertEqual("unknown_kind", err.get("code"))
            self.assertIn("allowed", err)

    def test_run_endpoint_returns_200_even_when_substep_reports_failure(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            with patch("core_memory.integrations.http.server.run_async_jobs") as mocked:
                mocked.return_value = {
                    "ok": False,
                    "schema_version": "core_memory.async_jobs.v1",
                    "semantic_run": {"ok": False, "ran": True},
                    "compaction_run": {"ok": True, "processed": 0},
                    "status_after": {"ok": True},
                    "errors": [{"code": "semantic_run_failed", "message": "Semantic rebuild step failed"}],
                }
                r = c.post(
                    "/v1/ops/async-jobs/run",
                    json={"root": root, "run_semantic": True, "max_compaction": 1},
                )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertFalse(data.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", data.get("schema_version"))
            errs = data.get("errors") or []
            self.assertTrue(any((e or {}).get("code") == "semantic_run_failed" for e in errs))


if __name__ == "__main__":
    unittest.main()
