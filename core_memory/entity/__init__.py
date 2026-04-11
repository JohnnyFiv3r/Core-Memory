"""Canonical entity registry surfaces."""

from .registry import (
    normalize_entity_alias,
    ensure_entity_registry_for_index,
    upsert_canonical_entity,
    resolve_entity_id,
    sync_bead_entities_for_index,
    load_entity_registry,
)

__all__ = [
    "normalize_entity_alias",
    "ensure_entity_registry_for_index",
    "upsert_canonical_entity",
    "resolve_entity_id",
    "sync_bead_entities_for_index",
    "load_entity_registry",
]
