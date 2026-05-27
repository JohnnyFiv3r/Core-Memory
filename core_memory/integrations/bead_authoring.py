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

Required semantic fields for a rich/retrieval-eligible bead:
- type: decision|goal|lesson|outcome|evidence|context|precedent|design_principle|reflection|correction|reversal
- title: short factual title, not a raw transcript prefix
- summary: 1-3 concise factual bullets
- retrieval_eligible: true only for durable memory worth indexing; false for thin beads
- retrieval_title: search-optimized title, required when retrieval_eligible=true
- retrieval_facts: concrete durable facts useful for later recall, required when retrieval_eligible=true
- entities: named people/projects/places/terms grounded in the turn
- topics: normalized topical labels grounded in the turn

Optional but encouraged fields:
- detail
- because / supporting_facts / evidence_refs
- state_change
- validity
- effective_from / effective_to / observed_at
- associations to visible prior beads when justified by evidence

Quality rules:
- Do not invent facts, evidence refs, dates, or associations.
- Prefer a thin non-retrieval bead over inflated weak memory.
- Set retrieval_eligible=true only when retrieval_title and retrieval_facts are useful.
- For thin beads, make retrieval_eligible=false and keep retrieval fields empty.
"""


def agent_authored_bead_spec() -> str:
    """Return adapter-consumable instructions for primary-agent bead authorship."""

    return BEAD_AUTHORING_SPEC


__all__ = ["BEAD_AUTHORING_SPEC", "agent_authored_bead_spec"]
