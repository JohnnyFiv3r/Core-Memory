import json
import tempfile
import unittest
from pathlib import Path


class TestHttpContinuityEndpoint(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_continuity_returns_json_by_default(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            from core_memory.persistence.store import MemoryStore
            MemoryStore(root)

            c = TestClient(app)
            r = c.get("/v1/memory/continuity", params={"root": root})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("json", data.get("format"))
            self.assertIn("authority", data)
            self.assertIn("records", data)

    def test_continuity_text_format(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            from core_memory.persistence.store import MemoryStore
            MemoryStore(root)

            c = TestClient(app)
            r = c.get("/v1/memory/continuity", params={"root": root, "format": "text"})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual("text", data.get("format"))
            self.assertIn("text", data)
            self.assertIn("count", data)

    def test_continuity_respects_max_items(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            from core_memory.persistence.store import MemoryStore
            MemoryStore(root)

            c = TestClient(app)
            r = c.get("/v1/memory/continuity", params={"root": root, "max_items": 5})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))

    def test_healthz_includes_version(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            from core_memory.persistence.store import MemoryStore
            MemoryStore(root)

            c = TestClient(app)
            r = c.get("/healthz", params={"root": root})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("version", data)
            self.assertIn("bead_count", data)

    def test_healthz_no_root(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        c = TestClient(app)
        r = c.get("/healthz")
        self.assertEqual(200, r.status_code)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("version", data)

    def test_healthz_reads_semantic_manifest(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            from core_memory.persistence.store import MemoryStore

            MemoryStore(str(root))
            semantic_dir = root / ".beads" / "semantic"
            semantic_dir.mkdir(parents=True, exist_ok=True)
            manifest = semantic_dir / "manifest.json"
            manifest.write_text(
                json.dumps({"backend": "faiss-openai", "provider": "openai"}),
                encoding="utf-8",
            )

            c = TestClient(app)
            r = c.get("/healthz", params={"root": str(root)})
            self.assertEqual(200, r.status_code)
            data = r.json()
            self.assertEqual("faiss-openai", data.get("semantic_backend"))
            self.assertEqual("openai", data.get("embeddings_provider"))


if __name__ == "__main__":
    unittest.main()
