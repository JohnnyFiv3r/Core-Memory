"""CrewAI memory implementations backed by Core Memory.

Maps Core Memory's bead promotion lifecycle to CrewAI's memory abstractions.
These classes can be used directly or passed to CrewAI's Crew configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from core_memory import process_turn_finalized
from core_memory.retrieval.tools import memory as memory_tools


class CoreMemoryShortTerm:
    """Short-term memory: open and candidate beads (recent, not yet promoted).

    Maps to CrewAI's ShortTermMemory interface — recent context from
    the current or recent sessions.
    """

    def __init__(self, root: str = ".", session_id: str | None = None):
        self.root = root
        self.session_id = session_id

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save a short-term memory via the canonical write path."""
        meta = metadata or {}
        authored_updates = meta.get("crawler_updates") if isinstance(meta.get("crawler_updates"), dict) else None
        process_turn_finalized(
            root=self.root,
            session_id=self.session_id or "crewai-default",
            turn_id=str(uuid.uuid4()),
            turns=[{"speaker": agent or "crewai", "role": "assistant", "content": value}],
            crawler_updates=authored_updates,
            authoring_mode="inline" if authored_updates is not None else "delegated",
            metadata={
                "type": meta.get("type", "context"),
                "tags": meta.get("tags", []),
                "source_turn_ids": meta.get("source_turn_ids", []),
            },
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
                "summary": (
                    " ".join(r.get("summary") or [])
                    if isinstance(r.get("summary"), list)
                    else str(r.get("summary", ""))
                ),
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

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save a long-term memory via the canonical write path.

        Promotion to "long-term" status happens via Core Memory's normal
        promotion lifecycle rather than being forced immediately.
        """
        meta = metadata or {}
        authored_updates = meta.get("crawler_updates") if isinstance(meta.get("crawler_updates"), dict) else None
        process_turn_finalized(
            root=self.root,
            session_id="crewai-long-term",
            turn_id=str(uuid.uuid4()),
            turns=[{"speaker": agent or "crewai", "role": "assistant", "content": value}],
            crawler_updates=authored_updates,
            authoring_mode="inline" if authored_updates is not None else "delegated",
            metadata={
                "type": meta.get("type", "lesson"),
                "tags": meta.get("tags", []),
                "source_turn_ids": meta.get("source_turn_ids", []),
            },
        )

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
                "summary": (
                    " ".join(r.get("summary") or [])
                    if isinstance(r.get("summary"), list)
                    else str(r.get("summary", ""))
                ),
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

    def save(self, value: str, metadata: dict[str, Any] | None = None, agent: str = "") -> None:
        """Save an entity memory via the canonical write path."""
        meta = metadata or {}
        authored_updates = meta.get("crawler_updates") if isinstance(meta.get("crawler_updates"), dict) else None
        process_turn_finalized(
            root=self.root,
            session_id="crewai-entity",
            turn_id=str(uuid.uuid4()),
            turns=[{"speaker": agent or "crewai", "role": "assistant", "content": value}],
            crawler_updates=authored_updates,
            authoring_mode="inline" if authored_updates is not None else "delegated",
            metadata={
                "type": "context",
                "tags": meta.get("tags", []),
                "source_turn_ids": meta.get("source_turn_ids", []),
            },
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
