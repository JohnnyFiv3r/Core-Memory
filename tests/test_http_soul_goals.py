from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestHttpSoulGoals(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_propose_approve_complete(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-goals-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)

            p = c.post("/v1/soul/goals/propose",
                       json={"root": root, "title": "Reduce onboarding friction", "goal_id": "g1"})
            self.assertEqual(200, p.status_code)
            self.assertEqual("candidate", p.json()["status"])

            a = c.post("/v1/soul/goals/approve", json={"root": root, "goal_id": "g1", "actor": "human"})
            self.assertEqual(200, a.status_code)
            self.assertEqual("endorsed", a.json()["to_state"])

            done = c.post("/v1/soul/goals/complete", json={"root": root, "goal_id": "g1"})
            self.assertEqual(200, done.status_code)
            self.assertEqual("completed", done.json()["to_state"])

    def test_invalid_transition_returns_400(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-goals-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            c.post("/v1/soul/goals/propose", json={"root": root, "title": "x", "goal_id": "g1"})
            r = c.post("/v1/soul/goals/complete", json={"root": root, "goal_id": "g1"})
            self.assertEqual(400, r.status_code)
            self.assertEqual("invalid_transition", r.json().get("error"))

    def test_unknown_goal_returns_400(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-goals-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post("/v1/soul/goals/decay", json={"root": root, "goal_id": "nope"})
            self.assertEqual(400, r.status_code)
            self.assertEqual("goal_not_found", r.json().get("error"))


if __name__ == "__main__":
    unittest.main()
