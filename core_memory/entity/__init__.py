"""Canonical entity registry surfaces."""

from .registry import (
    normalize_entity_alias,
    ensure_entity_registry_for_index,
    upsert_canonical_entity,
    resolve_entity_id,
    sync_bead_entities_for_index,
    load_entity_registry,
)
from .merge_flow import (
    suggest_entity_merge_proposals,
    list_entity_merge_proposals,
    decide_entity_merge_proposal,
)
from .retrieval import (
    infer_query_entity_context,
    expand_query_with_entities,
    bead_entity_match_score,
)

__all__ = [
    "normalize_entity_alias",
    "ensure_entity_registry_for_index",
    "upsert_canonical_entity",
    "resolve_entity_id",
    "sync_bead_entities_for_index",
    "load_entity_registry",
    "suggest_entity_merge_proposals",
    "list_entity_merge_proposals",
    "decide_entity_merge_proposal",
    "infer_query_entity_context",
    "expand_query_with_entities",
    "bead_entity_match_score",
]
