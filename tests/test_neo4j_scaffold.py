import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.integrations.neo4j.client import Neo4jDependencyError
from core_memory.integrations.neo4j.config import Neo4jConfig
from core_memory.integrations.neo4j.sync import neo4j_status, sync_to_neo4j
from core_memory.persistence.store import MemoryStore


class TestNeo4jSlice1Scaffold(unittest.TestCase):
    def test_config_from_env(self):
        with patch.dict(
            "os.environ",
            {
                "CORE_MEMORY_NEO4J_ENABLED": "1",
                "CORE_MEMORY_NEO4J_URI": "bolt://localhost:7687",
                "CORE_MEMORY_NEO4J_USER": "neo4j",
                "CORE_MEMORY_NEO4J_PASSWORD": "pw",
                "CORE_MEMORY_NEO4J_DATABASE": "neo4j",
                "CORE_MEMORY_NEO4J_DATASET": "team-a",
                "CORE_MEMORY_NEO4J_NODE_LABEL_MODE": "type_only",
                "CORE_MEMORY_NEO4J_EDGE_MODE": "typed",
                "CORE_MEMORY_NEO4J_TLS": "0",
                "CORE_MEMORY_NEO4J_TIMEOUT_MS": "9000",
            },
            clear=False,
        ):
            cfg = Neo4jConfig.from_env()
            self.assertTrue(cfg.enabled)
            self.assertEqual("bolt://localhost:7687", cfg.uri)
            self.assertEqual("team-a", cfg.dataset)
            self.assertEqual("type_only", cfg.node_label_mode)
            self.assertEqual("typed", cfg.edge_mode)
            self.assertFalse(cfg.tls)
            self.assertEqual(9000, cfg.timeout_ms)

    def test_status_reports_missing_dependency_with_actionable_error(self):
        cfg = Neo4jConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            user="neo4j",
            password="pw",
            database="neo4j",
            dataset="",
            node_label_mode="bead_plus_type",
            edge_mode="associated",
            tls=False,
            timeout_ms=1000,
        )
        with patch(
            "core_memory.integrations.neo4j.client.Neo4jClient._require_dependency",
            side_effect=Neo4jDependencyError("Install with: pip install core-memory[neo4j]"),
        ):
            out = neo4j_status(config=cfg)
            self.assertFalse(out.get("ok"))
            self.assertEqual("missing_dependency", out.get("status"))
            msg = str((out.get("error") or {}).get("message") or "")
            self.assertIn("core-memory[neo4j]", msg)

    def test_sync_dry_run_collects_projection(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            b1 = store.add_bead(type="decision", title="D1", summary=["a"], session_id="s1", source_turn_ids=["t1"])
            b2 = store.add_bead(type="outcome", title="O1", summary=["b"], session_id="s1", source_turn_ids=["t2"])
            store.link(source_id=b1, target_id=b2, relationship="supports", explanation="why")

            out = sync_to_neo4j(td, session_id="s1", dry_run=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual("dry_run", out.get("mode"))
            self.assertGreaterEqual(int(out.get("nodes_planned") or 0), 2)
            self.assertGreaterEqual(int(out.get("edges_planned") or 0), 1)

    def test_sync_non_dry_run_requires_enabled_flag(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(td)
            cfg = Neo4jConfig(
                enabled=False,
                uri="",
                user="",
                password="",
                database="neo4j",
                dataset="",
                node_label_mode="bead_plus_type",
                edge_mode="associated",
                tls=True,
                timeout_ms=5000,
            )
            out = sync_to_neo4j(td, config=cfg, dry_run=False)
            self.assertFalse(out.get("ok"))
            errs = out.get("errors") or []
            self.assertTrue(errs)
            self.assertEqual("neo4j_disabled", (errs[0] or {}).get("code"))


if __name__ == "__main__":
    unittest.main()
