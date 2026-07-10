"""Public agent-authored bead contract surface.

Adapters should inject this guidance into the primary agent's hot path when they
expect Core Memory to persist semantic turn memory. Core Memory validates and
persists authored fields; it should not silently re-author semantics by default.
"""

from __future__ import annotations

from core_memory.schema.agent_authoring_spec import BEAD_AUTHORING_SPEC


def agent_authored_bead_spec() -> str:
    """Return adapter-consumable instructions for primary-agent bead authorship."""

    return BEAD_AUTHORING_SPEC


__all__ = ["BEAD_AUTHORING_SPEC", "agent_authored_bead_spec"]
