from __future__ import annotations

from typing import Protocol, runtime_checkable

from core_memory.persistence.backend import BackendCapabilities


@runtime_checkable
class GraphBackend(Protocol):
    """Protocol for graph backends providing causal traversal and write hooks."""

    name: str

    def capabilities(self) -> BackendCapabilities: ...

    def health(self) -> dict: ...

    def traverse(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int = 16,
    ) -> list[dict]: ...

    def on_bead_written(self, bead: dict) -> None: ...

    def on_association_written(self, assoc: dict) -> None: ...

    def on_bead_retracted(self, bead_id: str) -> None: ...

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict: ...

    def close(self) -> None: ...


class NullGraphBackend:
    """Fallback graph backend — no-ops everywhere, triggers Python causal walker."""

    name = "null"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def health(self) -> dict:
        return {"ok": True, "backend": "null"}

    def traverse(
        self,
        seed_ids: list[str],
        edge_types: list[str] | None,
        max_hops: int,
        max_chains: int = 16,
    ) -> list[dict]:
        return []

    def on_bead_written(self, bead: dict) -> None:
        pass

    def on_association_written(self, assoc: dict) -> None:
        pass

    def on_bead_retracted(self, bead_id: str) -> None:
        pass

    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict:
        return {"synced_beads": 0, "synced_associations": 0}

    def close(self) -> None:
        pass
