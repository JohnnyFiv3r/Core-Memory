"""Thin integration adapters for external orchestrators."""

from .api import IntegrationContext, emit_turn_finalized, emit_turn_finalized_from_envelope

__all__ = [
    "IntegrationContext",
    "emit_turn_finalized",
    "emit_turn_finalized_from_envelope",
]
