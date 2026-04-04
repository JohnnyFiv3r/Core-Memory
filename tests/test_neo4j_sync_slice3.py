import tempfile
import unittest
from unittest.mock import patch

from core_memory.integrations.neo4j.config import Neo4jConfig
from core_memory.integrations.neo4j.sync import sync_to_neo4j
from core_memory.persistence.store import MemoryStore


class TestNeo4jSyncSlice3(unittest.TestCase):
    def _enabled_config(self) -> Neo4jConfig:
        return Neo4jConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            user="neo4j",
            password="pw",
            database="neo4j",
            tls=False,
            timeout_ms=1000,
        )

    def test_sync_exec_calls_upsert_and_returns_counts(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="decision", title="d", summary=["x"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="outcome", title="o", summary=["y"], session_id="s1", source_turn_ids=["t2"])
            s.link(source_id=b1, target_id=b2, relationship="supports", explanation="why")

            with patch(
                "core_memory.integrations.neo4j.sync.Neo4jClient.upsert_projection",
                return_value={
                    "ok": True,
                    "database": "neo4j",
                    "nodes_upserted": 2,
                    "edges_upserted": 1,
                    "nodes_pruned": 0,
                    "edges_pruned": 0,
                    "warnings": [],
                    "errors": [],
                },
            ) as up:
                out = sync_to_neo4j(td, session_id="s1", config=self._enabled_config(), dry_run=False)

            self.assertTrue(out.get("ok"))
            self.assertEqual(2, int(out.get("nodes_upserted") or 0))
            self.assertEqual(1, int(out.get("edges_upserted") or 0))
            self.assertEqual(1, up.call_count)

    def test_sync_dedupes_projection_before_upsert(self):
        projection = {
            "nodes": [
                {"labels": ["Bead"], "properties": {"bead_id": "b1", "type": "decision"}},
                {"labels": ["Bead"], "properties": {"bead_id": "b1", "type": "decision"}},
            ],
            "edges": [
                {
                    "type": "ASSOCIATED",
                    "start_bead_id": "b1",
                    "end_bead_id": "b2",
                    "properties": {"association_id": "assoc-1", "relationship": "supports"},
                },
                {
                    "type": "ASSOCIATED",
                    "start_bead_id": "b1",
                    "end_bead_id": "b2",
                    "properties": {"association_id": "assoc-1", "relationship": "supports"},
                },
            ],
        }
        captured: dict = {}

        def _fake_upsert(self, *, nodes, edges, prune=False, scope=None):
            captured["nodes"] = list(nodes)
            captured["edges"] = list(edges)
            return {
                "ok": True,
                "database": "neo4j",
                "nodes_upserted": len(nodes),
                "edges_upserted": len(edges),
                "nodes_pruned": 0,
                "edges_pruned": 0,
                "warnings": [],
                "errors": [],
            }

        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.integrations.neo4j.sync._collect_projection", return_value=projection
        ), patch("core_memory.integrations.neo4j.sync.Neo4jClient.upsert_projection", new=_fake_upsert):
            out = sync_to_neo4j(td, config=self._enabled_config(), dry_run=False)

        self.assertTrue(out.get("ok"))
        self.assertEqual(1, len(captured.get("nodes") or []))
        self.assertEqual(1, len(captured.get("edges") or []))
        warns = set(out.get("warnings") or [])
        self.assertIn("neo4j_projection_duplicate_nodes_deduped", warns)
        self.assertIn("neo4j_projection_duplicate_edges_deduped", warns)

    def test_sync_failure_isolated(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.integrations.neo4j.sync.Neo4jClient.upsert_projection",
            side_effect=RuntimeError("boom"),
        ):
            out = sync_to_neo4j(td, config=self._enabled_config(), dry_run=False)

        self.assertFalse(out.get("ok"))
        err = (out.get("errors") or [{}])[0]
        self.assertEqual("neo4j_sync_failed", err.get("code"))


if __name__ == "__main__":
    unittest.main()
