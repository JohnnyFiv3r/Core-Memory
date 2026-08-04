from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestHttpGoalProgress(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_async_producer_and_status_endpoints(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            client = TestClient(app)

            queued = client.post(
                "/v1/memory/goal-progress",
                json={
                    "root": root,
                    "evidence_bead_ids": ["evidence-1"],
                    "goal_bead_ids": ["goal-1"],
                    "trigger": "host",
                },
            )
            self.assertEqual(200, queued.status_code)
            self.assertTrue(queued.json().get("ok"), queued.json())
            self.assertEqual("goal-progress", queued.json().get("kind"), queued.json())

            status = client.get("/v1/memory/goal-progress/status", params={"root": root})
            self.assertEqual(200, status.status_code)
            body = status.json()
            self.assertTrue(body.get("ok"), body)
            self.assertEqual("memory.goal_progress_status.v1", body.get("contract"))
            self.assertTrue(body.get("live_producer_active"))

    def test_inline_backfill_rejects_an_invalid_cursor(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            client = TestClient(app)
            response = client.post(
                "/v1/memory/goal-progress/backfill",
                json={
                    "root": str(Path(td) / "memory"),
                    "cursor": "not-a-cursor",
                    "run_inline": True,
                },
            )
            self.assertEqual(400, response.status_code)
            self.assertEqual("invalid_cursor", response.json().get("error"))


if __name__ == "__main__":
    unittest.main()
