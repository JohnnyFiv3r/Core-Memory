from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.geometry import (
    GEOMETRY_NODE_SHAPE_VERSION,
    GEOMETRY_SCHEMA,
    build_geometry_manifest,
)


class TestHttpDreamerGeometry(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def test_geometry_served_from_manifest(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-geo-") as td:
            root = str(Path(td) / "memory")
            store = MemoryStore(root=root)
            g = store.add_bead(type="goal", title="G", summary=["s"], goal_id="g1", session_id="s1", entities=["acme"])
            d = store.add_bead(type="decision", title="D", summary=["s"], because=["y"],
                               detail="d", session_id="s2", entities=["scope"])
            store.link(d, g, "supports")
            build_geometry_manifest(root)

            c = TestClient(app)
            resp = c.get("/v1/dreamer/geometry", params={"root": root})
            self.assertEqual(200, resp.status_code)
            payload = resp.json()
            self.assertTrue(payload.get("ok"))
            self.assertTrue(payload.get("present"))
            self.assertEqual(GEOMETRY_SCHEMA, payload.get("schema"))
            self.assertEqual(GEOMETRY_NODE_SHAPE_VERSION, payload.get("node_shape_version"))
            self.assertEqual(2, payload.get("node_count"))
            self.assertEqual(1, payload.get("edge_count"))
            node = (payload.get("nodes") or [{}])[0]
            self.assertTrue({"title", "created_at", "timestamp", "entities"} <= set(node.keys()))
            self.assertEqual({"src", "dst", "rel", "strength", "provenance"},
                             set((payload.get("edges") or [{}])[0].keys()))

            alias = c.get("/v1/memory/projection/geometry", params={"root": root})
            self.assertEqual(200, alias.status_code)
            self.assertEqual(payload, alias.json())

    def test_geometry_absent_before_build(self):
        from fastapi.testclient import TestClient
        from core_memory.integrations.http.server import app

        with tempfile.TemporaryDirectory(prefix="cm-http-geo-") as td:
            root = str(Path(td) / "memory")
            c = TestClient(app)
            resp = c.get("/v1/dreamer/geometry", params={"root": root})
            self.assertEqual(200, resp.status_code)
            payload = resp.json()
            self.assertTrue(payload.get("ok"))
            self.assertFalse(payload.get("present"))
            self.assertEqual(GEOMETRY_SCHEMA, payload.get("schema"))
            self.assertEqual(GEOMETRY_NODE_SHAPE_VERSION, payload.get("node_shape_version"))

            alias = c.get("/v1/memory/projection/geometry", params={"root": root})
            self.assertEqual(200, alias.status_code)
            self.assertFalse(alias.json().get("present"))


if __name__ == "__main__":
    unittest.main()
