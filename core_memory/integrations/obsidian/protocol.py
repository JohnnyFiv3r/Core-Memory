from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BeadSyncTarget(Protocol):
    """Write-mirror protocol for outgoing sync targets (Obsidian vault, etc.).

    Distinct from GraphBackend: no traversal, no capabilities negotiation.
    Fire-and-forget write hooks only — never block the local write path.
    """

    name: str

    def on_bead_written(self, bead: dict) -> None: ...
    def on_association_written(self, assoc: dict) -> None: ...
    def on_bead_retracted(self, bead_id: str) -> None: ...
    def sync_from_storage(self, beads: list[dict], associations: list[dict]) -> dict: ...
    def close(self) -> None: ...
