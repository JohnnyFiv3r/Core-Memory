from __future__ import annotations

"""Public agent-authored bead contract surface.

Adapters should inject this guidance into the primary agent's hot path when they
expect Core Memory to persist semantic turn memory. Core Memory validates and
persists authored fields; it should not silently re-author semantics by default.
"""

BEAD_AUTHORING_SPEC = """Core Memory agent-authored bead contract

For each finalized top-level turn, author exactly one current-turn bead when the
turn contains memory-worthy semantic content. Core Memory owns structural fields
(ids, timestamps, source refs, persistence, indexing); the agent/adapter owns the
meaning-bearing fields.

Required semantic fields:
- type: decision|goal|lesson|outcome|evidence|context|precedent|design_principle|reflection|correction|reversal
- title: short factual title, not a raw transcript prefix
- summary: 1-3 concise factual bullets
- entities: named people/projects/places/terms grounded in the turn

Optional but encouraged fields:
- detail
- because / supporting_facts / evidence_refs
- state_change
- validity
- effective_from / effective_to / observed_at
- associations to visible prior beads when justified by evidence

Quality rules:
- Every bead is indexed. Write entities and supporting_facts for durable recall.
- Do not invent facts, evidence refs, dates, or associations.
- For thin turns, keep semantic fields minimal but still author title and summary.
"""


def agent_authored_bead_spec() -> str:
    """Return adapter-consumable instructions for primary-agent bead authorship."""

    return BEAD_AUTHORING_SPEC


__all__ = ["BEAD_AUTHORING_SPEC", "agent_authored_bead_spec"]
