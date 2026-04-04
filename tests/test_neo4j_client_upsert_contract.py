import unittest
from unittest.mock import patch

from core_memory.integrations.neo4j.client import Neo4jClient
from core_memory.integrations.neo4j.config import Neo4jConfig


class _FakeRunResult:
    def __init__(self, payload=None):
        self._payload = payload or {"ok": 1}

    def single(self):
        return dict(self._payload)


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self._sink.append((str(query), dict(params)))
        q = str(query)
        if "RETURN count(r) AS n" in q:
            return _FakeRunResult({"n": 2})
        if "RETURN count(b) AS n" in q:
            return _FakeRunResult({"n": 1})
        return _FakeRunResult({"ok": 1})


class _FakeDriver:
    def __init__(self, sink):
        self._sink = sink

    def session(self, database=None):
        self._sink.append(("__session__", {"database": database}))
        return _FakeSession(self._sink)

    def close(self):
        self._sink.append(("__close__", {}))


class TestNeo4jClientUpsertContract(unittest.TestCase):
    def test_upsert_projection_uses_merge_queries(self):
        cfg = Neo4jConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            user="neo4j",
            password="pw",
            database="neo4j",
            tls=False,
            timeout_ms=1000,
        )
        client = Neo4jClient(cfg)
        sink = []

        nodes = [
            {
                "labels": ["Bead", "Decision"],
                "properties": {"bead_id": "b1", "type": "decision", "title": "T"},
            }
        ]
        edges = [
            {
                "type": "ASSOCIATED",
                "start_bead_id": "b1",
                "end_bead_id": "b2",
                "properties": {"association_id": "assoc-1", "relationship": "supports"},
            }
        ]

        with patch.object(client, "_open_driver", return_value=_FakeDriver(sink)):
            out = client.upsert_projection(nodes=nodes, edges=edges, prune=False, scope={"session_id": "s1"})

        self.assertTrue(out.get("ok"))
        self.assertEqual(1, int(out.get("nodes_upserted") or 0))
        self.assertEqual(1, int(out.get("edges_upserted") or 0))

        sql = "\n".join(q for q, _ in sink if q not in {"__session__", "__close__"})
        self.assertIn("MERGE (b:Bead", sql)
        self.assertIn("MERGE (s)-[r:ASSOCIATED", sql)

    def test_upsert_projection_prune_executes_delete_queries(self):
        cfg = Neo4jConfig(
            enabled=True,
            uri="bolt://localhost:7687",
            user="neo4j",
            password="pw",
            database="neo4j",
            tls=False,
            timeout_ms=1000,
        )
        client = Neo4jClient(cfg)
        sink = []

        nodes = [{"labels": ["Bead"], "properties": {"bead_id": "b1", "type": "decision"}}]
        edges = [
            {
                "type": "ASSOCIATED",
                "start_bead_id": "b1",
                "end_bead_id": "b2",
                "properties": {"association_id": "assoc-1", "relationship": "supports"},
            }
        ]

        with patch.object(client, "_open_driver", return_value=_FakeDriver(sink)):
            out = client.upsert_projection(nodes=nodes, edges=edges, prune=True, scope={"session_id": "s1"})

        self.assertTrue(out.get("ok"))
        self.assertGreater(int(out.get("edges_pruned") or 0), 0)
        self.assertGreater(int(out.get("nodes_pruned") or 0), 0)
        sql = "\n".join(q for q, _ in sink if q not in {"__session__", "__close__"})
        self.assertIn("cm_owner", sql)
        self.assertIn("DELETE r RETURN count(r) AS n", sql)
        self.assertIn("DETACH DELETE b RETURN count(b) AS n", sql)


if __name__ == "__main__":
    unittest.main()
