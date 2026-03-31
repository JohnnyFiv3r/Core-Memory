"""Thin integration adapters for external orchestrators.

Import note:
- Keep package import side effects minimal to avoid CLI/runtime circular imports.
- Public surfaces are exposed lazily via ``__getattr__``.
"""

__all__ = [
    "IntegrationContext",
    "emit_turn_finalized",
    "emit_turn_finalized_from_envelope",
    "get_turn",
    "get_turn_tools",
    "get_adjacent_turns",
    "hydrate_bead_sources",
    "rebuild_turn_indexes",
    "backfill_bead_session_ids",
]


def __getattr__(name: str):
    if name in {
        "IntegrationContext",
        "emit_turn_finalized",
        "emit_turn_finalized_from_envelope",
        "get_turn",
        "get_turn_tools",
        "get_adjacent_turns",
        "hydrate_bead_sources",
    }:
        from . import api as _api
        return getattr(_api, name)

    if name in {"rebuild_turn_indexes", "backfill_bead_session_ids"}:
        from . import migration as _migration
        return getattr(_migration, name)

    raise AttributeError(name)
