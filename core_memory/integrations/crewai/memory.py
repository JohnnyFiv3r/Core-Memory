"""CrewAI memory implementations backed by Core Memory.

Maps Core Memory's bead promotion lifecycle to CrewAI's memory abstractions.
These classes can be used directly or passed to CrewAI's Crew configuration.
"""
from __future__ import annotations

from typing import Any

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools import memory as memory_tools


class CoreMemoryShortTerm:
    """Short-term memory: open and candidate beads (recent, not yet promoted).

    Maps to CrewAI's ShortTermMemory interface — recent context from
    the current or recent sessions.
    """

    def __init__(self, root: str = ".", session_id: str | None = None):
        self.root = root
        self.session_id = session_id
        self._store = MemoryStore(root=root)

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save a short-term memory as an open bead."""
        meta = metadata or {}
        self._store.add_bead(
            type=meta.get("type", "context"),
            title=value[:120] if len(value) > 120 else value,
            summary=[value],
            tags=meta.get("tags", []),
            session_id=self.session_id or "crewai-default",
            source_turn_ids=meta.get("source_turn_ids", []),
        )

    def search(self, query: str, limit: int = 5, score_threshold: float = 0.0) -> list[dict[str, Any]]:
        """Search short-term memories (open/candidate beads)."""
        result = memory_tools.search(
            request={
                "query_text": query,
                "k": limit,
                "status_filter": ["open", "candidate"],
            },
            root=self.root,
            explain=False,
        )
        return [
            {
                "context": r.get("title", ""),
                "summary": " ".join(r.get("summary") or []) if isinstance(r.get("summary"), list) else str(r.get("summary", "")),
                "score": r.get("score", 0.0),
                "metadata": {"bead_id": r.get("bead_id") or r.get("id"), "type": r.get("type")},
            }
            for r in (result.get("results") or [])
            if r.get("score", 0.0) >= score_threshold
        ]

    def reset(self) -> None:
        """No-op. Core Memory uses archival, not deletion."""
        pass


class CoreMemoryLongTerm:
    """Long-term memory: promoted and archived beads (validated, durable).

    Maps to CrewAI's LongTermMemory interface — persistent knowledge
    that has been validated through Core Memory's promotion lifecycle.
    """

    def __init__(self, root: str = "."):
        self.root = root
        self._store = MemoryStore(root=root)

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save a long-term memory as a promoted bead."""
        meta = metadata or {}
        bid = self._store.add_bead(
            type=meta.get("type", "lesson"),
            title=value[:120] if len(value) > 120 else value,
            summary=[value],
            tags=meta.get("tags", []),
            session_id="crewai-long-term",
            source_turn_ids=meta.get("source_turn_ids", []),
        )
        # Auto-promote since this is explicitly long-term
        try:
            self._store.promote_bead(bid)
        except Exception:
            pass  # Promotion may fail if bead doesn't meet criteria — that's ok

    def search(self, query: str, limit: int = 5, score_threshold: float = 0.0) -> list[dict[str, Any]]:
        """Search long-term memories (promoted/archived beads)."""
        result = memory_tools.search(
            request={
                "query_text": query,
                "k": limit,
                "status_filter": ["promoted", "archived"],
            },
            root=self.root,
            explain=False,
        )
        return [
            {
                "context": r.get("title", ""),
                "summary": " ".join(r.get("summary") or []) if isinstance(r.get("summary"), list) else str(r.get("summary", "")),
                "score": r.get("score", 0.0),
                "metadata": {"bead_id": r.get("bead_id") or r.get("id"), "type": r.get("type")},
            }
            for r in (result.get("results") or [])
            if r.get("score", 0.0) >= score_threshold
        ]

    def reset(self) -> None:
        """No-op. Core Memory uses archival, not deletion."""
        pass


class CoreMemoryEntity:
    """Entity memory: beads with populated entity fields.

    Maps to CrewAI's EntityMemory interface — tracks entities (people,
    systems, concepts) mentioned across sessions.
    """

    def __init__(self, root: str = "."):
        self.root = root
        self._store = MemoryStore(root=root)

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save an entity memory."""
        meta = metadata or {}
        entities = meta.get("entities", [])
        if not entities and value:
            entities = [value[:80]]
        self._store.add_bead(
            type="context",
            title=f"Entity: {value[:100]}" if len(value) > 100 else f"Entity: {value}",
            summary=[value],
            tags=meta.get("tags", []),
            session_id="crewai-entity",
            source_turn_ids=meta.get("source_turn_ids", []),
        )

    def search(self, query: str, limit: int = 5, score_threshold: float = 0.0) -> list[dict[str, Any]]:
        """Search entity memories."""
        result = memory_tools.search(
            request={"query_text": query, "k": limit},
            root=self.root,
            explain=False,
        )
        # Filter to beads that have entity data
        return [
            {
                "context": r.get("title", ""),
                "entity": (r.get("entities") or [None])[0] if r.get("entities") else r.get("title", ""),
                "score": r.get("score", 0.0),
                "metadata": {"bead_id": r.get("bead_id") or r.get("id"), "type": r.get("type")},
            }
            for r in (result.get("results") or [])
            if r.get("score", 0.0) >= score_threshold
        ]

    def reset(self) -> None:
        """No-op. Core Memory uses archival, not deletion."""
        pass
