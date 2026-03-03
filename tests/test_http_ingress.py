import json
import tempfile
import unittest
from pathlib import Path


class TestHttpIngress(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_http_turn_finalized_emits_event(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/memory/turn-finalized",
                json={
                    "root": root,
                    "session_id": "s1",
                    "turn_id": "t1",
                    "user_query": "u",
                    "assistant_final": "a",
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("accepted"))
            self.assertTrue(str(data.get("event_id", "")).startswith("mev-"))

            events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
            rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(1, len(rows))

    def test_http_idempotent_same_turn_id(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            body = {
                "root": root,
                "session_id": "s1",
                "turn_id": "t1",
                "user_query": "u",
                "assistant_final": "a",
            }
            r1 = c.post("/v1/memory/turn-finalized", json=body)
            r2 = c.post("/v1/memory/turn-finalized", json=body)
            self.assertEqual(200, r1.status_code)
            self.assertEqual(400, r2.status_code)

            state_file = Path(root) / ".beads" / "events" / "memory-pass-state.json"
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("s1:t1", state)


if __name__ == "__main__":
    unittest.main()
