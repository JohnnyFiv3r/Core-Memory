import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.dreamer.geometry import (
    GEOMETRY_NODE_SHAPE_VERSION,
    GEOMETRY_NODE_SHAPE_VERSION_V1,
    GEOMETRY_SCHEMA,
    GEOMETRY_SCHEMA_V1,
    build_geometry_manifest,
    read_geometry_manifest,
)


def _seed(td):
    store = MemoryStore(root=td)
    g = store.add_bead(type="goal", title="Ship simply", summary=["s"], goal_id="g1",
                       because=["x"], session_id="s1", entities=["acme"])
    d = store.add_bead(type="decision", title="Cut scope", summary=["s"], because=["y"],
                       detail="d", session_id="s2", entities=["scope"])
    e = store.add_bead(type="evidence", title="User asked for less", summary=["s"],
                       detail="d", session_id="s3")
    store.link(d, g, "supports")
    store.link(e, g, "supports")
    return store, g, d, e


class TestGeometryManifest(unittest.TestCase):
    def test_build_shapes_nodes_and_edges(self):
        with tempfile.TemporaryDirectory() as td:
            _seed(td)
            m = build_geometry_manifest(td)
            self.assertEqual(GEOMETRY_SCHEMA, m["schema"])
            self.assertEqual(GEOMETRY_NODE_SHAPE_VERSION, m["node_shape_version"])
            self.assertEqual(3, m["node_count"])
            self.assertEqual(2, m["edge_count"])
            node = m["nodes"][0]
            self.assertTrue({"id", "type", "status", "assembly_depth", "title", "created_at", "timestamp", "entities"} <= set(node.keys()))
            self.assertIsInstance(node["entities"], list)
            self.assertEqual(node["created_at"], node["timestamp"])
            edge = m["edges"][0]
            self.assertEqual({"src", "dst", "rel", "strength", "provenance"}, set(edge.keys()))
            # Every node carries a numeric depth in [0, 1].
            for n in m["nodes"]:
                self.assertTrue(0.0 <= float(n["assembly_depth"]) <= 1.0)

    def test_read_serves_from_disk_after_build(self):
        with tempfile.TemporaryDirectory() as td:
            _seed(td)
            build_geometry_manifest(td)
            out = read_geometry_manifest(td)
            self.assertTrue(out["ok"])
            self.assertTrue(out["present"])
            self.assertEqual(3, out["node_count"])
            self.assertEqual(2, out["edge_count"])

    def test_read_without_build_reports_absent(self):
        with tempfile.TemporaryDirectory() as td:
            out = read_geometry_manifest(td)
            self.assertTrue(out["ok"])
            self.assertFalse(out["present"])
            self.assertEqual(GEOMETRY_SCHEMA, out["schema"])
            self.assertEqual(GEOMETRY_NODE_SHAPE_VERSION, out["node_shape_version"])
            self.assertEqual(0, out["node_count"])
            self.assertEqual([], out["nodes"])

    def test_read_legacy_v1_manifest_signals_old_node_shape(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".beads" / "events" / "dreamer-geometry.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "schema": GEOMETRY_SCHEMA_V1,
                "generated_at": "2026-06-16T00:00:00+00:00",
                "node_count": 1,
                "edge_count": 0,
                "total_bead_count": 1,
                "truncated": False,
                "limit": 5000,
                "nodes": [{"id": "b1", "type": "evidence", "status": "active", "assembly_depth": 0.0}],
                "edges": [],
            }), encoding="utf-8")
            out = read_geometry_manifest(td)
            self.assertTrue(out["ok"])
            self.assertTrue(out["present"])
            self.assertEqual(GEOMETRY_SCHEMA_V1, out["schema"])
            self.assertEqual(GEOMETRY_NODE_SHAPE_VERSION_V1, out["node_shape_version"])
            self.assertTrue(out["legacy_node_shape"])
            self.assertNotIn("title", out["nodes"][0])

    def test_inactive_associations_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            store, g, d, e = _seed(td)
            # Manually mark one association inactive in the index.
            idx_path = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx["associations"][0]["status"] = "superseded"
            idx_path.write_text(json.dumps(idx), encoding="utf-8")
            m = build_geometry_manifest(td)
            self.assertEqual(1, m["edge_count"])

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as td:
            m = build_geometry_manifest(td)
            self.assertEqual(0, m["node_count"])
            self.assertEqual(0, m["edge_count"])

    def test_limit_caps_nodes_and_edges_consistently(self):
        # With more beads than the limit, the manifest caps emitted nodes to the
        # scored set, never serving placeholder-0.0 depths for uncomputed beads,
        # and never emitting an edge to a node outside the manifest.
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(root=td)
            ids = [store.add_bead(type="decision", title=f"d{i}", summary=["s"],
                                  because=["x"], detail="d", topics=["t"], session_id=f"s{i}")
                   for i in range(6)]
            store.link(ids[0], ids[1], "supports")
            m = build_geometry_manifest(td, limit=3)
            self.assertEqual(3, m["node_count"])
            self.assertTrue(m["truncated"])
            self.assertEqual(6, m["total_bead_count"])
            emitted = {n["id"] for n in m["nodes"]}
            for e in m["edges"]:
                self.assertIn(e["src"], emitted)
                self.assertIn(e["dst"], emitted)


class TestGeometryWiring(unittest.TestCase):
    def test_dreamer_run_builds_manifest(self):
        from core_memory.runtime.queue.side_effect_queue import process_side_effect_event
        with tempfile.TemporaryDirectory() as td:
            _seed(td)
            out = process_side_effect_event(root=td, kind="dreamer-run", payload={"mode": "suggest"})
            self.assertIn("geometry", out)
            self.assertTrue(out["geometry"]["ok"])
            self.assertEqual(3, out["geometry"]["node_count"])
            # Manifest is now served from disk.
            self.assertTrue(read_geometry_manifest(td)["present"])


if __name__ == "__main__":
    unittest.main()
