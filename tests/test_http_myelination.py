from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestHttpMyelination(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_manifest_absent_then_present(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)

            absent = c.get("/v1/myelination/manifest", params={"root": root})
            self.assertEqual(200, absent.status_code)
            self.assertFalse(absent.json()["present"])

            p = Path(root) / ".beads" / "events" / "myelination-manifest.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "schema": "core_memory.myelination_manifest.v2",
                "enabled": True,
                "bonus_by_edge_key": {"a|supports|b": 0.2},
            }), encoding="utf-8")

            present = c.get("/v1/myelination/manifest", params={"root": root})
            self.assertEqual(200, present.status_code)
            body = present.json()
            self.assertTrue(body["present"])
            self.assertEqual(0.2, body["bonus_by_edge_key"]["a|supports|b"])

    def test_report_endpoint(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-myel-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            r = c.get("/v1/myelination/report", params={"root": root, "top": 5})
            self.assertEqual(200, r.status_code)
            self.assertEqual("core_memory.myelination_experiment.v1", r.json()["schema"])


if __name__ == "__main__":
    unittest.main()
