from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates


class TestHttpDreamerCandidatesSlice62A(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_list_and_decide_candidate(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-dc-") as td:
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
                        "confidence": 0.7,
                    }
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )

            c = TestClient(app)
            listed = c.get("/v1/ops/dreamer/candidates", params={"root": root, "status": "pending", "limit": 5})
            self.assertEqual(200, listed.status_code)
            payload = listed.json()
            self.assertTrue(payload.get("ok"))
            self.assertGreaterEqual(int(payload.get("count") or 0), 1)
            cid = str(((payload.get("results") or [{}])[0].get("id") or ""))
            self.assertTrue(cid)

            dec = c.post(
                "/v1/ops/dreamer/candidates/decide",
                json={
                    "root": root,
                    "candidate_id": cid,
                    "decision": "reject",
                    "reviewer": "qa",
                },
            )
            self.assertEqual(200, dec.status_code)
            d = dec.json()
            self.assertTrue(d.get("ok"))
            self.assertEqual("rejected", d.get("status"))

    def test_decide_missing_candidate_returns_400(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-dc-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            dec = c.post(
                "/v1/ops/dreamer/candidates/decide",
                json={
                    "root": root,
                    "candidate_id": "does-not-exist",
                    "decision": "accept",
                },
            )
            self.assertEqual(400, dec.status_code)
            payload = dec.json()
            self.assertFalse(payload.get("ok"))
            self.assertEqual("candidate_not_found", ((payload.get("error") or {}).get("code") or ""))


if __name__ == "__main__":
    unittest.main()
