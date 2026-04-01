import json
import os
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

    def test_http_runtime_execute_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            s = MemoryStore(root)
            s.add_bead(type="decision", title="Candidate-first promotion", summary=["promotion workflow"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])

            c = TestClient(app)
            r = c.post(
                "/v1/memory/execute",
                json={
                    "root": root,
                    "request": {
                        "raw_query": "remember candidate-first promotion",
                        "intent": "remember",
                        "constraints": {"require_structural": False},
                        "facets": {"topic_keys": ["promotion_workflow"]},
                        "k": 5,
                    },
                    "explain": True,
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertTrue(data.get("results"))
            self.assertIn("grounding", data)

    def test_http_trace_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            s = MemoryStore(root)
            s.add_bead(type="decision", title="Candidate-first promotion", summary=["promotion workflow"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])
            c = TestClient(app)
            r = c.post("/v1/memory/trace", json={"root": root, "query": "why candidate-first promotion", "k": 5})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("anchors", data)

            r2 = c.post(
                "/v1/memory/trace",
                json={
                    "root": root,
                    "query": "why candidate-first promotion",
                    "k": 5,
                    "hydration": {"turn_sources": "cited_turns", "adjacent_before": 1, "adjacent_after": 1},
                },
            )
            self.assertEqual(200, r2.status_code)
            data2 = r2.json()
            self.assertIn("hydration", data2)

    def test_http_session_flush_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            c.post(
                "/v1/memory/turn-finalized",
                json={
                    "root": root,
                    "session_id": "s1",
                    "turn_id": "t1",
                    "user_query": "u",
                    "assistant_final": "a",
                },
            )
            r = c.post("/v1/memory/session-flush", json={"root": root, "session_id": "s1", "source": "http_test"})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(bool(data.get("ok")))

    def test_http_classify_intent_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        c = TestClient(app)
        r = c.post('/v1/memory/classify-intent', json={'query': 'why did promotion inflation happen'})
        self.assertEqual(200, r.status_code)
        data = r.json()
        self.assertEqual('causal', data.get('intent_class'))
        self.assertTrue(bool(data.get('causal_intent')))

    def test_http_reason_endpoint_with_pins(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            s = MemoryStore(root)
            bid = s.add_bead(type="decision", title="Candidate-first promotion", summary=["promotion workflow"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])

            c = TestClient(app)
            r = c.post(
                "/v1/memory/reason",
                json={
                    "root": root,
                    "query": "why candidate-first promotion",
                    "k": 6,
                    "pinned_incident_ids": ["promotion_inflation_2026q1"],
                    "pinned_topic_keys": ["promotion_workflow"],
                    "pinned_bead_ids": [bid],
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(bool(data.get("ok")))
            intent = data.get("intent") or {}
            self.assertIn("pinned_incident_ids", intent)
            self.assertIn("pinned_topic_keys", intent)
            self.assertIn("pinned_bead_ids", intent)

    def test_http_execute_deterministic_response(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            s = MemoryStore(root)
            s.add_bead(type="decision", title="Candidate-first promotion", summary=["promotion workflow"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])
            c = TestClient(app)
            body = {
                "root": root,
                "request": {
                    "raw_query": "remember candidate-first promotion",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "facets": {"topic_keys": ["promotion_workflow"]},
                    "k": 5,
                },
                "explain": True,
            }
            r1 = c.post('/v1/memory/execute', json=body)
            r2 = c.post('/v1/memory/execute', json=body)
            self.assertEqual(200, r1.status_code)
            self.assertEqual(200, r2.status_code)
            d1 = r1.json()
            d2 = r2.json()
            self.assertEqual(d1.get('snapped'), d2.get('snapped'))
            self.assertEqual(d1.get('confidence'), d2.get('confidence'))
            self.assertEqual(d1.get('warnings'), d2.get('warnings'))
            self.assertEqual(d1.get('next_action'), d2.get('next_action'))
            self.assertEqual(d1.get('results'), d2.get('results'))
            self.assertEqual(d1.get('chains'), d2.get('chains'))

    def test_http_auth_token_protection(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http import server as srv

        old = os.environ.get("CORE_MEMORY_HTTP_TOKEN")
        os.environ["CORE_MEMORY_HTTP_TOKEN"] = "secret"
        try:
            c = TestClient(srv.app)
            r1 = c.get("/v1/memory/search-form")
            self.assertEqual(401, r1.status_code)
            r2 = c.get("/v1/memory/search-form", headers={"Authorization": "Bearer secret"})
            self.assertEqual(200, r2.status_code)
            self.assertEqual("memory_search_form.v1", (r2.json() or {}).get("schema_version"))
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_HTTP_TOKEN", None)
            else:
                os.environ["CORE_MEMORY_HTTP_TOKEN"] = old

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
                "user_query": "important decision completed confirmed",
                "assistant_final": "important decision completed confirmed",
            }
            r1 = c.post("/v1/memory/turn-finalized", json=body)
            r2 = c.post("/v1/memory/turn-finalized", json=body)
            self.assertEqual(200, r1.status_code)
            self.assertEqual(200, r2.status_code)

            events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
            rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
