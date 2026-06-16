"""SOUL — agent-authored self-model (PRD: docs/PRD/soul-files.md)."""

from core_memory.soul.dreamer_bridge import propose_soul_from_dreamer
from core_memory.soul.goals import (
    abandon_goal,
    approve_goal,
    complete_goal,
    decay_goal,
    propose_goal,
    reject_goal,
)
from core_memory.soul.injection import soul_injection
from core_memory.soul.integrity import soul_integrity_check, soul_integrity_repair
from core_memory.soul.store import (
    DEFAULT_SUBJECT,
    SOUL_FILES,
    SOUL_REVISION_SCHEMA,
    approve_soul_update,
    current_soul_entries,
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
    "abandon_goal",
    "approve_goal",
    "complete_goal",
    "decay_goal",
    "propose_goal",
    "reject_goal",
    "approve_soul_update",
    "list_soul_files",
    "current_soul_entries",
    "propose_soul_from_dreamer",
    "propose_soul_update",
    "read_soul_file",
    "reject_soul_update",
    "soul_history",
    "soul_injection",
    "soul_integrity_check",
    "soul_integrity_repair",
]
