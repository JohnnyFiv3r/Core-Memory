import tempfile
import unittest
from pathlib import Path


class TestHttpMCPTypedReads(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_http_mcp_query_current_state(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store_claim_ops import write_claims_to_bead

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            write_claims_to_bead(
                root,
                "bead1",
                [
                    {
                        "id": "c1",
                        "claim_kind": "profile",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "UTC",
                        "reason_text": "stated",
                        "confidence": 0.9,
                    }
                ],
            )
            c = TestClient(app)
            r = c.post(
                "/v1/mcp/query-current-state",
                json={"root": root, "slot_key": "user:timezone", "k": 5},
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertEqual("mcp.query_current_state.v1", data.get("contract"))
            self.assertTrue(data.get("ok"))

    def test_http_mcp_query_temporal_window_and_causal_chain(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            s = MemoryStore(root)
            s.add_bead(type="decision", title="Deploy", summary=["deploy changed"], session_id="main", source_turn_ids=["t1"])
            c = TestClient(app)

            r1 = c.post(
                "/v1/mcp/query-temporal-window",
                json={
                    "root": root,
                    "query": "what changed",
                    "window_start": "2026-01-01T00:00:00Z",
                    "window_end": "2026-01-31T23:59:59Z",
                    "k": 5,
                },
            )
            self.assertEqual(200, r1.status_code)
            self.assertEqual("mcp.query_temporal_window.v1", (r1.json() or {}).get("contract"))

            r2 = c.post(
                "/v1/mcp/query-causal-chain",
                json={"root": root, "query": "why deploy changed", "k": 5},
            )
            self.assertEqual(200, r2.status_code)
            self.assertEqual("mcp.query_causal_chain.v1", (r2.json() or {}).get("contract"))

    def test_http_mcp_query_contradictions(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app
        from core_memory.persistence.store_claim_ops import write_claims_to_bead, write_claim_updates_to_bead

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            write_claims_to_bead(
                root,
                "bead1",
                [
                    {
                        "id": "c1",
                        "claim_kind": "preference",
                        "subject": "user",
                        "slot": "drink",
                        "value": "coffee",
                        "reason_text": "stated",
                        "confidence": 0.9,
                    }
                ],
            )
            write_claim_updates_to_bead(
                root,
                "bead2",
                [
                    {
                        "id": "u1",
                        "decision": "conflict",
                        "target_claim_id": "c1",
                        "subject": "user",
                        "slot": "drink",
                        "reason_text": "contradiction",
                        "trigger_bead_id": "bead2",
                    }
                ],
            )
            c = TestClient(app)
            r = c.post(
                "/v1/mcp/query-contradictions",
                json={"root": root, "slot_key": "user:drink", "k": 5},
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertEqual("mcp.query_contradictions.v1", data.get("contract"))
            self.assertTrue(list(data.get("claim_conflicts") or []))


if __name__ == "__main__":
    unittest.main()
