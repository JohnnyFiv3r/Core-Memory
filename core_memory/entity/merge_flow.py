"""Domain-facing entity merge review exports.

The durable index implementation lives in ``core_memory.persistence`` so
store methods can delegate downward without importing the entity layer.
"""

from core_memory.persistence.entity_merge_flow import (
    apply_entity_merge_direct,
    apply_entity_merge_for_index,
    decide_entity_merge_proposal,
    decide_entity_merge_proposal_for_index,
    ensure_entity_merge_proposals_for_index,
    list_entity_merge_proposals,
    list_entity_merge_proposals_for_index,
    suggest_entity_merge_proposals,
    suggest_entity_merge_proposals_for_index,
)

__all__ = [
    "ensure_entity_merge_proposals_for_index",
    "suggest_entity_merge_proposals_for_index",
    "apply_entity_merge_for_index",
    "decide_entity_merge_proposal_for_index",
    "list_entity_merge_proposals_for_index",
    "suggest_entity_merge_proposals",
    "decide_entity_merge_proposal",
    "list_entity_merge_proposals",
    "apply_entity_merge_direct",
]
