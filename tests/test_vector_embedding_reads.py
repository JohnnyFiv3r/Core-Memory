from __future__ import annotations

import unittest
from types import SimpleNamespace

from core_memory.retrieval.vector_backend import (
    ChromaDBBackend,
    PgvectorBackend,
    QdrantBackend,
    _bead_id_to_qdrant_id,
)


class _QdrantClient:
    def __init__(self) -> None:
        self.requested_ids: list[str] = []

    def retrieve(self, **kwargs):
        self.requested_ids = list(kwargs["ids"])
        return [
            SimpleNamespace(
                id=kwargs["ids"][0],
                payload={"bead_id": "b1"},
                vector={"dense": [0.1, 0.2]},
            )
        ]


class _ChromaCollection:
    def get(self, **kwargs):
        return {"ids": ["b2"], "embeddings": [[0.3, 0.4]]}


class _PgCursor:
    def fetchall(self):
        return [("b3", "[0.5, 0.6]")]


class _PgConnection:
    def execute(self, *_args, **_kwargs):
        return _PgCursor()


class TestVectorEmbeddingReads(unittest.TestCase):
    def test_qdrant_recovers_original_bead_id_and_named_vector(self):
        backend = QdrantBackend.__new__(QdrantBackend)
        backend._client = _QdrantClient()
        backend._collection = "core-memory"

        result = backend.get_embeddings(["b1"])

        self.assertEqual([_bead_id_to_qdrant_id("b1")], backend._client.requested_ids)
        self.assertEqual({"b1": [0.1, 0.2]}, result)

    def test_chroma_returns_dense_vectors(self):
        backend = ChromaDBBackend.__new__(ChromaDBBackend)
        backend._collection = _ChromaCollection()
        self.assertEqual({"b2": [0.3, 0.4]}, backend.get_embeddings(["b2"]))

    def test_pgvector_parses_vector_text(self):
        backend = PgvectorBackend.__new__(PgvectorBackend)
        backend._conn = _PgConnection()
        backend._table = "core_memory_beads"
        self.assertEqual({"b3": [0.5, 0.6]}, backend.get_embeddings(["b3"]))


if __name__ == "__main__":
    unittest.main()
