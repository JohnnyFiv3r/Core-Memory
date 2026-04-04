"""StorageBackend abstraction for Core Memory persistence.

Two implementations:
- JsonFileBackend: wraps current index.json logic (default, no behavior change)
- SqliteBackend: .beads/memory.db with indexed tables for beads and associations

Session JSONL files remain the source of truth. Backends replace only the
projection cache (index.json equivalent).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for persistence backends."""

    def load_index(self) -> dict[str, Any]:
        """Load the full index as a dict (backward-compatible surface)."""
        ...

    def save_index(self, index: dict[str, Any]) -> None:
        """Persist the full index dict."""
        ...

    def get_bead(self, bead_id: str) -> dict[str, Any] | None:
        """Retrieve a single bead by ID. Returns None if not found."""
        ...

    def put_bead(self, bead: dict[str, Any]) -> None:
        """Insert or update a single bead."""
        ...

    def delete_bead(self, bead_id: str) -> bool:
        """Delete a bead by ID. Returns True if it existed."""
        ...

    def query_beads(self, filters: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Query beads with optional filters (type, status, session_id, tags)."""
        ...

    def get_associations(self) -> list[dict[str, Any]]:
        """Get all associations."""
        ...

    def put_association(self, assoc: dict[str, Any]) -> None:
        """Add or update an association."""
        ...

    def get_associations_for_bead(self, bead_id: str) -> list[dict[str, Any]]:
        """Get associations where bead_id is source or target."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        ...


class JsonFileBackend:
    """Wraps the existing index.json file-based persistence. Default backend."""

    def __init__(self, beads_dir: Path):
        self._beads_dir = beads_dir
        self._index_path = beads_dir / "index.json"

    def load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {
                "beads": {},
                "associations": [],
                "stats": {"total_beads": 0, "total_associations": 0, "created_at": datetime.now(timezone.utc).isoformat()},
                "projection": {"mode": "session_first_projection_cache", "rebuilt_at": None},
            }
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            from core_memory.persistence.store import DiagnosticError
            raise DiagnosticError(
                f"Corrupt JSON file: {self._index_path}",
                recovery="Rebuild from sessions: MemoryStore(root).rebuild_index_projection_from_sessions()",
            )

    def save_index(self, index: dict[str, Any]) -> None:
        from core_memory.persistence.io_utils import atomic_write_json
        atomic_write_json(self._index_path, index)

    def get_bead(self, bead_id: str) -> dict[str, Any] | None:
        index = self.load_index()
        return (index.get("beads") or {}).get(bead_id)

    def put_bead(self, bead: dict[str, Any]) -> None:
        index = self.load_index()
        index.setdefault("beads", {})[bead["id"]] = bead
        stats = index.setdefault("stats", {})
        stats["total_beads"] = len(index["beads"])
        self.save_index(index)

    def delete_bead(self, bead_id: str) -> bool:
        index = self.load_index()
        existed = bead_id in (index.get("beads") or {})
        if existed:
            del index["beads"][bead_id]
            index.setdefault("stats", {})["total_beads"] = len(index["beads"])
            self.save_index(index)
        return existed

    def query_beads(self, filters: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        index = self.load_index()
        beads = list((index.get("beads") or {}).values())
        if filters:
            if "type" in filters:
                beads = [b for b in beads if b.get("type") == filters["type"]]
            if "status" in filters:
                beads = [b for b in beads if b.get("status") == filters["status"]]
            if "session_id" in filters:
                beads = [b for b in beads if b.get("session_id") == filters["session_id"]]
        return beads[:limit]

    def get_associations(self) -> list[dict[str, Any]]:
        return list(self.load_index().get("associations") or [])

    def put_association(self, assoc: dict[str, Any]) -> None:
        index = self.load_index()
        associations = list(index.get("associations") or [])
        associations.append(assoc)
        index["associations"] = associations
        index.setdefault("stats", {})["total_associations"] = len(associations)
        self.save_index(index)

    def get_associations_for_bead(self, bead_id: str) -> list[dict[str, Any]]:
        return [
            a for a in self.get_associations()
            if a.get("source_bead") == bead_id or a.get("target_bead") == bead_id
        ]

    def get_stats(self) -> dict[str, Any]:
        return dict(self.load_index().get("stats") or {})


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS beads (
    id TEXT PRIMARY KEY,
    type TEXT,
    status TEXT,
    session_id TEXT,
    created_at TEXT,
    promoted_at TEXT,
    data TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);
CREATE INDEX IF NOT EXISTS idx_beads_session ON beads(session_id);
CREATE INDEX IF NOT EXISTS idx_beads_created ON beads(created_at);

CREATE TABLE IF NOT EXISTS associations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_bead TEXT,
    target_bead TEXT,
    relationship TEXT,
    data TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assoc_source ON associations(source_bead);
CREATE INDEX IF NOT EXISTS idx_assoc_target ON associations(target_bead);
CREATE INDEX IF NOT EXISTS idx_assoc_rel ON associations(relationship);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteBackend:
    """SQLite-backed persistence. Opt-in via backend='sqlite' or CORE_MEMORY_BACKEND=sqlite.

    Uses .beads/memory.db with indexed tables for beads and associations.
    Session JSONL files remain the source of truth — this replaces only
    the projection cache.
    """

    def __init__(self, beads_dir: Path):
        self._beads_dir = beads_dir
        self._db_path = beads_dir / "memory.db"
        beads_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

        # In-memory cache for load_index() calls (avoid full table scan on every read)
        self._cache: dict[str, Any] | None = None

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass

    def _invalidate_cache(self) -> None:
        self._cache = None

    def load_index(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache

        cur = self._conn.execute("SELECT id, data FROM beads")
        beads = {}
        for row in cur.fetchall():
            bead = json.loads(row[1])
            beads[row[0]] = bead

        cur = self._conn.execute("SELECT data FROM associations")
        associations = [json.loads(row[0]) for row in cur.fetchall()]

        meta = {}
        cur = self._conn.execute("SELECT key, value FROM meta")
        for row in cur.fetchall():
            meta[row[0]] = json.loads(row[1])

        index = {
            "beads": beads,
            "associations": associations,
            "stats": meta.get("stats", {"total_beads": len(beads), "total_associations": len(associations)}),
            "projection": meta.get("projection", {"mode": "session_first_projection_cache", "rebuilt_at": None}),
        }
        self._cache = index
        return index

    def save_index(self, index: dict[str, Any]) -> None:
        """Full index save — used during rebuild and bulk operations."""
        self._conn.execute("DELETE FROM beads")
        self._conn.execute("DELETE FROM associations")

        beads = index.get("beads") or {}
        for bead_id, bead in beads.items():
            self._conn.execute(
                "INSERT OR REPLACE INTO beads (id, type, status, session_id, created_at, promoted_at, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    bead_id,
                    bead.get("type"),
                    bead.get("status"),
                    bead.get("session_id"),
                    bead.get("created_at"),
                    bead.get("promoted_at"),
                    json.dumps(bead, ensure_ascii=False, default=str),
                ),
            )

        for assoc in (index.get("associations") or []):
            self._conn.execute(
                "INSERT INTO associations (source_bead, target_bead, relationship, data) VALUES (?, ?, ?, ?)",
                (
                    assoc.get("source_bead"),
                    assoc.get("target_bead"),
                    assoc.get("relationship") or assoc.get("rel"),
                    json.dumps(assoc, ensure_ascii=False, default=str),
                ),
            )

        stats = index.get("stats") or {}
        stats.setdefault("total_beads", len(beads))
        stats.setdefault("total_associations", len(index.get("associations") or []))
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("stats", json.dumps(stats, ensure_ascii=False, default=str)),
        )
        projection = index.get("projection") or {}
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("projection", json.dumps(projection, ensure_ascii=False, default=str)),
        )
        self._conn.commit()
        self._cache = index

    def get_bead(self, bead_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT data FROM beads WHERE id = ?", (bead_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def put_bead(self, bead: dict[str, Any]) -> None:
        bead_id = bead["id"]
        self._conn.execute(
            "INSERT OR REPLACE INTO beads (id, type, status, session_id, created_at, promoted_at, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                bead_id,
                bead.get("type"),
                bead.get("status"),
                bead.get("session_id"),
                bead.get("created_at"),
                bead.get("promoted_at"),
                json.dumps(bead, ensure_ascii=False, default=str),
            ),
        )
        self._conn.commit()
        self._invalidate_cache()

    def delete_bead(self, bead_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM beads WHERE id = ?", (bead_id,))
        self._conn.commit()
        self._invalidate_cache()
        return cur.rowcount > 0

    def query_beads(self, filters: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if filters:
            if "type" in filters:
                clauses.append("type = ?")
                params.append(filters["type"])
            if "status" in filters:
                clauses.append("status = ?")
                params.append(filters["status"])
            if "session_id" in filters:
                clauses.append("session_id = ?")
                params.append(filters["session_id"])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur = self._conn.execute(f"SELECT data FROM beads{where} ORDER BY created_at DESC LIMIT ?", params)
        return [json.loads(row[0]) for row in cur.fetchall()]

    def get_associations(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT data FROM associations")
        return [json.loads(row[0]) for row in cur.fetchall()]

    def put_association(self, assoc: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO associations (source_bead, target_bead, relationship, data) VALUES (?, ?, ?, ?)",
            (
                assoc.get("source_bead"),
                assoc.get("target_bead"),
                assoc.get("relationship") or assoc.get("rel"),
                json.dumps(assoc, ensure_ascii=False, default=str),
            ),
        )
        self._conn.commit()
        self._invalidate_cache()

    def get_associations_for_bead(self, bead_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT data FROM associations WHERE source_bead = ? OR target_bead = ?",
            (bead_id, bead_id),
        )
        return [json.loads(row[0]) for row in cur.fetchall()]

    def get_stats(self) -> dict[str, Any]:
        cur = self._conn.execute("SELECT value FROM meta WHERE key = 'stats'")
        row = cur.fetchone()
        if row:
            return json.loads(row[0])
        bead_count = self._conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        assoc_count = self._conn.execute("SELECT COUNT(*) FROM associations").fetchone()[0]
        return {"total_beads": bead_count, "total_associations": assoc_count}


def create_backend(beads_dir: Path, backend: str = "json") -> StorageBackend:
    """Factory for creating storage backends.

    Args:
        beads_dir: Path to the .beads directory
        backend: "json" (default) or "sqlite"
    """
    import os
    backend = (os.environ.get("CORE_MEMORY_BACKEND") or backend).strip().lower()
    if backend == "sqlite":
        return SqliteBackend(beads_dir)
    return JsonFileBackend(beads_dir)
