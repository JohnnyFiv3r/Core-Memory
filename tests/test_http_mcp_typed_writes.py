import tempfile
import unittest
from pathlib import Path


class TestHttpMCPTypedWrites(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_http_mcp_write_turn_finalized(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/mcp/write-turn-finalized",
                json={
                    "root": root,
                    "session_id": "s1",
                    "turn_id": "t1",
                    "turns": [
                        {"speaker": "user", "role": "user", "content": "remember this"},
                        {"speaker": "assistant", "role": "assistant", "content": "noted"},
                    ],
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("memory.turn_finalized_receipt.v2", data.get("contract"))
            self.assertEqual("committed", data.get("semantic_status"))

    def test_http_mcp_write_turn_finalized_rejects_path_traversal_ids(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            outside = Path(td) / "outside"
            c = TestClient(app)
            r = c.post(
                "/v1/mcp/write-turn-finalized",
                json={
                    "root": root,
                    "session_id": "x/../../outside/payload",
                    "turn_id": "t1",
                    "turns": [{"speaker": "user", "role": "user", "content": "attacker bytes"}],
                },
            )
            self.assertEqual(400, r.status_code)
            self.assertIn("invalid_session_id", str(r.json()))
            self.assertFalse(outside.exists())

    def test_http_mcp_apply_reviewed_proposal(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app
        from core_memory.runtime.dreamer.candidates import enqueue_dreamer_candidates

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            enqueue_dreamer_candidates(
                root=root,
                associations=[
                    {
                        "source": "b1",
                        "target": "b2",
                        "relationship": "contradicts",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.8,
                    }
                ],
                run_metadata={"run_id": "http-mcp2", "mode": "suggest"},
            )
            c = TestClient(app)
            listed = c.get("/v1/ops/dreamer/candidates", params={"root": root, "status": "pending", "limit": 5})
            cid = str(((listed.json().get("results") or [{}])[0].get("id") or ""))
            self.assertTrue(cid)

            r = c.post(
                "/v1/mcp/apply-reviewed-proposal",
                json={
                    "root": root,
                    "candidate_id": cid,
                    "decision": "reject",
                    "reviewer": "qa",
                    "notes": "reject",
                    "apply": True,
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("mcp.apply_reviewed_proposal.v1", data.get("contract"))
            self.assertEqual("rejected", data.get("status"))

    def test_http_mcp_submit_entity_merge_proposal(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.post(
                "/v1/mcp/submit-entity-merge-proposal",
                json={
                    "root": root,
                    "source_entity_id": "entity-a",
                    "target_entity_id": "entity-b",
                    "source_bead_id": "bead-a",
                    "target_bead_id": "bead-b",
                    "confidence": 0.95,
                    "reviewer": "qa",
                },
            )
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("mcp.submit_entity_merge_proposal.v1", data.get("contract"))
            cid = str(data.get("candidate_id") or "")
            self.assertTrue(cid)

            listed = c.get("/v1/ops/dreamer/candidates", params={"root": root, "status": "pending", "limit": 50})
            rows = listed.json().get("results") or []
            self.assertTrue(any(str(rw.get("id") or "") == cid for rw in rows))


if __name__ == "__main__":
    unittest.main()
