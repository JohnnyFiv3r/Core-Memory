"""Thin integration adapters for external orchestrators."""

from .api import (
    IntegrationContext,
    emit_turn_finalized,
    emit_turn_finalized_from_envelope,
    get_turn,
    get_turn_tools,
    get_adjacent_turns,
    hydrate_bead_sources,
)

__all__ = [
    "IntegrationContext",
    "emit_turn_finalized",
    "emit_turn_finalized_from_envelope",
    "get_turn",
    "get_turn_tools",
    "get_adjacent_turns",
    "hydrate_bead_sources",
]
