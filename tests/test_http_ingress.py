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
            self.assertEqual("canonical_in_process", data.get("authority_path"))
            self.assertEqual(1, int(data.get("processed") or 0))

            events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
            rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(1, len(rows))

            idx_file = Path(root) / ".beads" / "index.json"
            idx = json.loads(idx_file.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len((idx.get("beads") or {})), 1)

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
            r1 = c.post(
                "/v1/memory/execute",
                json={"request": {"raw_query": "x", "intent": "remember", "k": 3}, "explain": True},
            )
            self.assertEqual(401, r1.status_code)
            r2 = c.post(
                "/v1/memory/execute",
                json={"request": {"raw_query": "x", "intent": "remember", "k": 3}, "explain": True},
                headers={"Authorization": "Bearer secret"},
            )
            self.assertEqual(200, r2.status_code)
            self.assertTrue((r2.json() or {}).get("ok") is not False)
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_HTTP_TOKEN", None)
            else:
                os.environ["CORE_MEMORY_HTTP_TOKEN"] = old

    def test_removed_http_surfaces_return_not_found(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        c = TestClient(app)
        r1 = c.get("/v1/memory/search-form")
        self.assertEqual(404, r1.status_code)

        r2 = c.post("/v1/memory/reason", json={"query": "why"})
        self.assertEqual(404, r2.status_code)

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

    def test_http_tenant_isolation_for_stateful_read_endpoints(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app, _resolve_root
        from core_memory.persistence.store import MemoryStore
        from core_memory.persistence.rolling_record_store import write_rolling_records

        with tempfile.TemporaryDirectory() as td:
            base_root = str(Path(td) / "memory")
            tenant_a = "tenant-a"
            tenant_b = "tenant-b"

            tenant_a_root = _resolve_root(base_root, tenant_a)
            tenant_b_root = _resolve_root(base_root, tenant_b)

            # Seed isolated tenant stores + default store.
            MemoryStore(tenant_a_root).add_bead(
                type="decision",
                title="alpha_tenant_only",
                summary=["alpha tenant marker"],
                session_id="main",
                source_turn_ids=["ta1"],
            )
            MemoryStore(tenant_b_root).add_bead(
                type="decision",
                title="beta_tenant_only",
                summary=["beta tenant marker"],
                session_id="main",
                source_turn_ids=["tb1"],
            )
            MemoryStore(base_root).add_bead(
                type="decision",
                title="default_tenant_only",
                summary=["default marker"],
                session_id="main",
                source_turn_ids=["td1"],
            )

            # Seed continuity records per namespace.
            write_rolling_records(
                tenant_a_root,
                records=[{"type": "decision", "title": "alpha_tenant_only", "summary": ["alpha tenant marker"]}],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )
            write_rolling_records(
                tenant_b_root,
                records=[{"type": "decision", "title": "beta_tenant_only", "summary": ["beta tenant marker"]}],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )

            c = TestClient(app)

            # search isolation
            r_a = c.post(
                "/v1/memory/search",
                json={"root": base_root, "form_submission": {"query_text": "alpha_tenant_only", "k": 5}},
                headers={"X-Tenant-Id": tenant_a},
            )
            self.assertEqual(200, r_a.status_code)
            self.assertGreaterEqual(len((r_a.json() or {}).get("results") or []), 1)

            r_default = c.post(
                "/v1/memory/search",
                json={"root": base_root, "form_submission": {"query_text": "alpha_tenant_only", "k": 5}},
            )
            self.assertEqual(200, r_default.status_code)
            self.assertEqual(0, len((r_default.json() or {}).get("results") or []))

            r_b = c.post(
                "/v1/memory/search",
                json={"root": base_root, "form_submission": {"query_text": "alpha_tenant_only", "k": 5}},
                headers={"X-Tenant-Id": tenant_b},
            )
            self.assertEqual(200, r_b.status_code)
            self.assertEqual(0, len((r_b.json() or {}).get("results") or []))

            # execute isolation
            ex_a = c.post(
                "/v1/memory/execute",
                json={"root": base_root, "request": {"raw_query": "alpha_tenant_only", "intent": "remember", "k": 5}, "explain": True},
                headers={"X-Tenant-Id": tenant_a},
            )
            self.assertEqual(200, ex_a.status_code)
            self.assertTrue(bool((ex_a.json() or {}).get("results")))

            ex_default = c.post(
                "/v1/memory/execute",
                json={"root": base_root, "request": {"raw_query": "alpha_tenant_only", "intent": "remember", "k": 5}, "explain": True},
            )
            self.assertEqual(200, ex_default.status_code)
            self.assertEqual([], (ex_default.json() or {}).get("results") or [])

            # trace isolation
            tr_a = c.post(
                "/v1/memory/trace",
                json={"root": base_root, "query": "alpha_tenant_only", "k": 5},
                headers={"X-Tenant-Id": tenant_a},
            )
            self.assertEqual(200, tr_a.status_code)
            self.assertTrue(bool((tr_a.json() or {}).get("anchors")))

            tr_b = c.post(
                "/v1/memory/trace",
                json={"root": base_root, "query": "alpha_tenant_only", "k": 5},
                headers={"X-Tenant-Id": tenant_b},
            )
            self.assertEqual(200, tr_b.status_code)
            self.assertEqual([], (tr_b.json() or {}).get("anchors") or [])

            # continuity isolation
            ct_a = c.get("/v1/memory/continuity", params={"root": base_root}, headers={"X-Tenant-Id": tenant_a})
            self.assertEqual(200, ct_a.status_code)
            recs_a = (ct_a.json() or {}).get("records") or []
            self.assertTrue(any(str(r.get("title") or "") == "alpha_tenant_only" for r in recs_a))

            ct_default = c.get("/v1/memory/continuity", params={"root": base_root})
            self.assertEqual(200, ct_default.status_code)
            recs_default = (ct_default.json() or {}).get("records") or []
            self.assertFalse(any(str(r.get("title") or "") == "alpha_tenant_only" for r in recs_default))

    def test_http_tenant_scopes_turn_finalized_and_session_flush_paths(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app, _resolve_root

        with tempfile.TemporaryDirectory() as td:
            base_root = str(Path(td) / "memory")
            tenant_a = "tenant-a"
            tenant_b = "tenant-b"
            tenant_a_root = Path(_resolve_root(base_root, tenant_a))
            tenant_b_root = Path(_resolve_root(base_root, tenant_b))

            c = TestClient(app)

            r = c.post(
                "/v1/memory/turn-finalized",
                json={
                    "root": base_root,
                    "session_id": "s1",
                    "turn_id": "t1",
                    "user_query": "tenant a write",
                    "assistant_final": "tenant a write",
                },
                headers={"X-Tenant-Id": tenant_a},
            )
            self.assertEqual(200, r.status_code)

            events_a = tenant_a_root / ".beads" / "events" / "memory-events.jsonl"
            events_b = tenant_b_root / ".beads" / "events" / "memory-events.jsonl"
            events_default = Path(base_root) / ".beads" / "events" / "memory-events.jsonl"
            self.assertTrue(events_a.exists())
            self.assertFalse(events_b.exists())
            self.assertFalse(events_default.exists())

            rf = c.post(
                "/v1/memory/session-flush",
                json={"root": base_root, "session_id": "s1", "source": "http_test"},
                headers={"X-Tenant-Id": tenant_a},
            )
            self.assertEqual(200, rf.status_code)
            checkpoints_a = tenant_a_root / ".beads" / "events" / "flush-checkpoints.jsonl"
            self.assertTrue(checkpoints_a.exists())

    def test_http_rejects_invalid_tenant_id_header(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/memory/execute",
                json={
                    "root": root,
                    "request": {"raw_query": "q", "intent": "remember", "k": 3},
                    "explain": True,
                },
                headers={"X-Tenant-Id": "../../other-root"},
            )
            self.assertEqual(400, r.status_code)
            self.assertEqual("invalid_tenant_id", (r.json() or {}).get("detail"))

    def test_http_returns_503_for_required_semantic_unavailable(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http import server as srv
        from core_memory.persistence.store import MemoryStore

        old_mode = os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE")
        old_provider = os.environ.get("CORE_MEMORY_EMBEDDINGS_PROVIDER")
        old_openai = os.environ.get("OPENAI_API_KEY")
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "required"
        os.environ["CORE_MEMORY_EMBEDDINGS_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = ""
        try:
            with tempfile.TemporaryDirectory() as td:
                root = str(Path(td) / "memory")
                s = MemoryStore(root)
                s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])

                c = TestClient(srv.app)
                r = c.post(
                    "/v1/memory/search",
                    json={
                        "root": root,
                        "form_submission": {"query_text": "A", "intent": "remember", "k": 5},
                        "explain": True,
                    },
                )
                self.assertEqual(503, r.status_code)
                self.assertEqual("semantic_backend_unavailable", ((r.json() or {}).get("error") or {}).get("code"))
        finally:
            if old_mode is None:
                os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
            else:
                os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = old_mode
            if old_provider is None:
                os.environ.pop("CORE_MEMORY_EMBEDDINGS_PROVIDER", None)
            else:
                os.environ["CORE_MEMORY_EMBEDDINGS_PROVIDER"] = old_provider
            if old_openai is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_openai


if __name__ == "__main__":
    unittest.main()
