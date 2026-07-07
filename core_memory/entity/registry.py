"""Domain-facing entity registry exports.

The durable index implementation lives in ``core_memory.persistence`` so
store write paths do not import upward into the entity layer.
"""

from core_memory.persistence.entity_registry import (
    ensure_entity_registry_for_index,
    load_entity_registry,
    normalize_entity_alias,
    register_speaker_alias,
    resolve_entity_id,
    save_entity_registry,
    sync_bead_entities_for_index,
    upsert_canonical_entity,
)

__all__ = [
    "normalize_entity_alias",
    "ensure_entity_registry_for_index",
    "upsert_canonical_entity",
    "resolve_entity_id",
    "sync_bead_entities_for_index",
    "register_speaker_alias",
    "load_entity_registry",
    "save_entity_registry",
]
