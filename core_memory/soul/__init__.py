"""SOUL — agent-authored self-model (PRD: docs/PRD/soul-files.md)."""

from core_memory.soul.injection import soul_injection
from core_memory.soul.store import (
    DEFAULT_SUBJECT,
    SOUL_FILES,
    SOUL_REVISION_SCHEMA,
    approve_soul_update,
    list_soul_files,
    propose_soul_update,
    read_soul_file,
    reject_soul_update,
    soul_history,
)

__all__ = [
    "DEFAULT_SUBJECT",
    "SOUL_FILES",
    "SOUL_REVISION_SCHEMA",
    "approve_soul_update",
    "list_soul_files",
    "propose_soul_update",
    "read_soul_file",
    "reject_soul_update",
    "soul_history",
    "soul_injection",
]
