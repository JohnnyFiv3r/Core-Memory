import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.engine import process_turn_finalized


class TestHttpInspectEndpoints(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_inspect_endpoints_smoke(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            process_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember db choice",
                assistant_final="postgres was chosen",
                origin="TEST",
                metadata={"source": "test"},
            )

            c = TestClient(app)

            rs = c.get("/v1/memory/inspect/state", params={"root": root, "session_id": "s1"})
            self.assertEqual(200, rs.status_code)
            js = rs.json()
            self.assertTrue(js.get("ok"))
            beads = list((js.get("memory") or {}).get("beads") or [])
            self.assertGreaterEqual(len(beads), 1)
            bead_id = str(beads[0].get("id") or "")

            rb = c.get(f"/v1/memory/inspect/beads/{bead_id}", params={"root": root})
            self.assertEqual(200, rb.status_code)
            self.assertTrue(rb.json().get("ok"))

            rh = c.get(f"/v1/memory/inspect/beads/{bead_id}/hydrate", params={"root": root})
            self.assertEqual(200, rh.status_code)
            self.assertTrue(rh.json().get("ok"))

            rc = c.get("/v1/memory/inspect/claim-slots/user/preferred_db", params={"root": root})
            self.assertEqual(200, rc.status_code)
            self.assertTrue(rc.json().get("ok"))

            rt = c.get("/v1/memory/inspect/turns", params={"root": root, "session_id": "s1", "limit": 5})
            self.assertEqual(200, rt.status_code)
            self.assertTrue(rt.json().get("ok"))
            self.assertTrue(isinstance(rt.json().get("items"), list))


if __name__ == "__main__":
    unittest.main()
