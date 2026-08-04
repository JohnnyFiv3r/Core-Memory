"""Canonical entity registry surfaces."""

from .merge_flow import (
    decide_entity_merge_proposal,
    list_entity_merge_proposals,
    suggest_entity_merge_proposals,
)
from .quality import is_meaningful_entity_label
from .registry import (
    ensure_entity_registry_for_index,
    load_entity_registry,
    normalize_entity_alias,
    resolve_entity_id,
    sync_bead_entities_for_index,
    upsert_canonical_entity,
)
from .retrieval import (
    bead_entity_match_score,
    expand_query_with_entities,
    infer_query_entity_context,
)

__all__ = [
    "is_meaningful_entity_label",
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
