"""VectorBackend protocol and implementations for filtered vector search.

FAISS has no metadata filtering. These backends support pre-filtering by
type/date/status before vector search.

Implementations:
- QdrantBackend: qdrant-client based (pip install core-memory[qdrant])
- ChromaDBBackend: chromadb based (pip install core-memory[chromadb])
- PgvectorBackend: psycopg + pgvector (pip install core-memory[pgvector])
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class VectorBackend(Protocol):
    """Protocol for vector search backends with metadata filtering."""

    def upsert(self, bead_id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        """Insert or update a bead's embedding with metadata."""
        ...

    def search(
        self,
        query_embedding: list[float],
        k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar beads with optional metadata filters.

        Filters support: type, status, session_id, created_after, created_before.
        Returns list of {"bead_id": str, "score": float, "metadata": dict}.
        """
        ...

    def delete(self, bead_id: str) -> None:
        """Delete a bead's embedding."""
        ...

    def count(self) -> int:
        """Return the number of indexed embeddings."""
        ...


class QdrantBackend:
    """Qdrant vector search backend.

    Requires: pip install core-memory[qdrant]
    """

    def __init__(self, collection_name: str = "core_memory_beads", url: str = "http://localhost:6333"):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError:
            raise ImportError("Qdrant backend requires: pip install core-memory[qdrant]")

        self._client = QdrantClient(url=url)
        self._collection = collection_name

        # Ensure collection exists
        collections = [c.name for c in self._client.get_collections().collections]
        if collection_name not in collections:
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            )

    def upsert(self, bead_id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        from qdrant_client.models import PointStruct

        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=bead_id, vector=embedding, payload=metadata)],
        )

    def search(
        self,
        query_embedding: list[float],
        k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

        qdrant_filter = None
        if filters:
            conditions = []
            if "type" in filters:
                conditions.append(FieldCondition(key="type", match=MatchValue(value=filters["type"])))
            if "status" in filters:
                conditions.append(FieldCondition(key="status", match=MatchValue(value=filters["status"])))
            if "session_id" in filters:
                conditions.append(FieldCondition(key="session_id", match=MatchValue(value=filters["session_id"])))
            if "created_after" in filters:
                conditions.append(FieldCondition(key="created_at", range=Range(gte=filters["created_after"])))
            if "created_before" in filters:
                conditions.append(FieldCondition(key="created_at", range=Range(lte=filters["created_before"])))
            if conditions:
                qdrant_filter = Filter(must=conditions)

        results = self._client.search(
            collection_name=self._collection,
            query_vector=query_embedding,
            limit=k,
            query_filter=qdrant_filter,
        )
        return [
            {"bead_id": str(r.id), "score": float(r.score), "metadata": dict(r.payload or {})}
            for r in results
        ]

    def delete(self, bead_id: str) -> None:
        from qdrant_client.models import PointIdsList
        self._client.delete(collection_name=self._collection, points_selector=PointIdsList(points=[bead_id]))

    def count(self) -> int:
        info = self._client.get_collection(self._collection)
        return info.points_count or 0


class ChromaDBBackend:
    """ChromaDB vector search backend.

    Requires: pip install core-memory[chromadb]
    """

    def __init__(self, collection_name: str = "core_memory_beads", persist_directory: str | None = None):
        try:
            import chromadb
        except ImportError:
            raise ImportError("ChromaDB backend requires: pip install core-memory[chromadb]")

        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
        else:
            self._client = chromadb.Client()
        self._collection = self._client.get_or_create_collection(name=collection_name)

    def upsert(self, bead_id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        # ChromaDB requires metadata values to be str/int/float/bool
        clean_meta = {}
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            elif v is not None:
                clean_meta[k] = str(v)
        self._collection.upsert(ids=[bead_id], embeddings=[embedding], metadatas=[clean_meta])

    def search(
        self,
        query_embedding: list[float],
        k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        where_filter = None
        if filters:
            conditions = {}
            for key in ("type", "status", "session_id"):
                if key in filters:
                    conditions[key] = filters[key]
            if conditions:
                if len(conditions) == 1:
                    key, val = next(iter(conditions.items()))
                    where_filter = {key: val}
                else:
                    where_filter = {"$and": [{k: v} for k, v in conditions.items()]}

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where_filter,
        )
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        return [
            {"bead_id": bid, "score": 1.0 - dist, "metadata": dict(meta or {})}
            for bid, dist, meta in zip(ids, distances, metadatas)
        ]

    def delete(self, bead_id: str) -> None:
        self._collection.delete(ids=[bead_id])

    def count(self) -> int:
        return self._collection.count()


class PgvectorBackend:
    """PostgreSQL + pgvector backend for filtered vector search.

    Requires: pip install core-memory[pgvector]
    (psycopg[binary] + pgvector extension on the database)

    Connection via CORE_MEMORY_PG_DSN env var or dsn parameter.
    """

    def __init__(self, dsn: str | None = None, table_name: str = "core_memory_beads", dimensions: int = 1536):
        import os
        try:
            import psycopg
        except ImportError:
            raise ImportError("pgvector backend requires: pip install core-memory[pgvector]")

        self._dsn = dsn or os.environ.get("CORE_MEMORY_PG_DSN", "")
        if not self._dsn:
            raise ValueError("PostgreSQL DSN required: set CORE_MEMORY_PG_DSN or pass dsn= parameter")
        self._table = table_name
        self._dim = dimensions
        self._conn = psycopg.connect(self._dsn, autocommit=True)

        # Ensure pgvector extension and table exist
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                bead_id TEXT PRIMARY KEY,
                embedding vector({self._dim}),
                type TEXT,
                status TEXT,
                session_id TEXT,
                created_at TEXT,
                metadata JSONB DEFAULT '{{}}'::jsonb
            )
        """)
        self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_type ON {self._table}(type)")
        self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_status ON {self._table}(status)")

    def upsert(self, bead_id: str, embedding: list[float], metadata: dict[str, Any]) -> None:
        self._conn.execute(
            f"""
            INSERT INTO {self._table} (bead_id, embedding, type, status, session_id, created_at, metadata)
            VALUES (%s, %s::vector, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (bead_id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                type = EXCLUDED.type,
                status = EXCLUDED.status,
                session_id = EXCLUDED.session_id,
                created_at = EXCLUDED.created_at,
                metadata = EXCLUDED.metadata
            """,
            (
                bead_id,
                str(embedding),
                metadata.get("type"),
                metadata.get("status"),
                metadata.get("session_id"),
                metadata.get("created_at"),
                json.dumps(metadata),
            ),
        )

    def search(
        self,
        query_embedding: list[float],
        k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = [str(query_embedding)]

        if filters:
            if "type" in filters:
                conditions.append("type = %s")
                params.append(filters["type"])
            if "status" in filters:
                conditions.append("status = %s")
                params.append(filters["status"])
            if "session_id" in filters:
                conditions.append("session_id = %s")
                params.append(filters["session_id"])
            if "created_after" in filters:
                conditions.append("created_at >= %s")
                params.append(filters["created_after"])
            if "created_before" in filters:
                conditions.append("created_at <= %s")
                params.append(filters["created_before"])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(k)

        cur = self._conn.execute(
            f"""
            SELECT bead_id, 1 - (embedding <=> %s::vector) AS score, metadata
            FROM {self._table}{where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            params[:1] + params + params[:1],
        )
        # Simplify the query params
        results = []
        for row in cur.fetchall():
            results.append({
                "bead_id": row[0],
                "score": float(row[1]),
                "metadata": json.loads(row[2]) if isinstance(row[2], str) else dict(row[2] or {}),
            })
        return results

    def delete(self, bead_id: str) -> None:
        self._conn.execute(f"DELETE FROM {self._table} WHERE bead_id = %s", (bead_id,))

    def count(self) -> int:
        cur = self._conn.execute(f"SELECT COUNT(*) FROM {self._table}")
        return cur.fetchone()[0]


def create_vector_backend(backend_type: str = "qdrant", **kwargs: Any) -> VectorBackend:
    """Factory for creating vector search backends.

    Args:
        backend_type: "qdrant", "chromadb", or "pgvector"
        **kwargs: Passed to the backend constructor
    """
    if backend_type == "qdrant":
        return QdrantBackend(**kwargs)
    if backend_type in ("chromadb", "chroma"):
        return ChromaDBBackend(**kwargs)
    if backend_type in ("pgvector", "postgres", "postgresql"):
        return PgvectorBackend(**kwargs)
    raise ValueError(f"Unknown vector backend: {backend_type}. Supported: qdrant, chromadb, pgvector")
