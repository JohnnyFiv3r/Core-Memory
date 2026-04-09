from __future__ import annotations

# Shared store/persistence surface constants
BEADS_DIR = ".beads"
TURNS_DIR = ".turns"
EVENTS_DIR = ".beads/events"
SESSION_FILE = "session-{id}.jsonl"
INDEX_FILE = "index.json"
HEADS_FILE = "heads.json"


class DiagnosticError(Exception):
    """Raised with recovery instructions when a persistence file is corrupt."""

    def __init__(self, message: str, recovery: str):
        self.recovery = recovery
        super().__init__(f"{message}\n  Recovery: {recovery}")


__all__ = [
    "BEADS_DIR",
    "TURNS_DIR",
    "EVENTS_DIR",
    "SESSION_FILE",
    "INDEX_FILE",
    "HEADS_FILE",
    "DiagnosticError",
]
