from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.soul.store import propose_soul_update


class TestHttpSoulIntegrity(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_check_and_repair(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-soul-int-") as td:
            root = str(Path(td) / "memory")
            propose_soul_update(root, target_file="GOALS.md", entry_key="blank",
                                content="  ", requires_approval=False)
            c = TestClient(app)

            check = c.post("/v1/soul/integrity/check", json={"root": root})
            self.assertEqual(200, check.status_code)
            payload = check.json()
            self.assertTrue(payload.get("ok"))
            self.assertEqual(1, payload.get("repairable_count"))

            repair = c.post("/v1/soul/integrity/repair", json={"root": root})
            self.assertEqual(200, repair.status_code)
            self.assertEqual(1, repair.json().get("repaired_count"))

            # Now clean.
            self.assertEqual(0, c.post("/v1/soul/integrity/check", json={"root": root}).json()["issue_count"])


if __name__ == "__main__":
    unittest.main()
