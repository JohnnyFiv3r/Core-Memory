"""Typed MCP integration surfaces.

These helpers expose practical, typed read operations that map onto canonical
Core Memory retrieval/runtime surfaces.
"""

from .typed_read import (
    MCP_TYPED_READ_TOOL_SCHEMAS,
    query_current_state,
    query_temporal_window,
    query_causal_chain,
    query_contradictions,
)
from .typed_write import (
    MCP_TYPED_WRITE_TOOL_SCHEMAS,
    write_turn_finalized,
    apply_reviewed_proposal,
    submit_entity_merge_proposal,
)

__all__ = [
    "MCP_TYPED_READ_TOOL_SCHEMAS",
    "query_current_state",
    "query_temporal_window",
    "query_causal_chain",
    "query_contradictions",
    "MCP_TYPED_WRITE_TOOL_SCHEMAS",
    "write_turn_finalized",
    "apply_reviewed_proposal",
    "submit_entity_merge_proposal",
]
