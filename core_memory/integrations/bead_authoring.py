from __future__ import annotations

"""Public agent-authored bead contract surface.

Adapters should inject this guidance into the primary agent's hot path when they
expect Core Memory to persist semantic turn memory. Core Memory validates and
persists authored fields; it should not silently re-author semantics by default.
"""

BEAD_AUTHORING_SPEC = """Core Memory agent-authored bead contract

For each finalized turn, author exactly one bead when the turn contains memory-worthy content.
Core Memory owns structural fields (ids, timestamps, refs, indexing). You own meaning-bearing fields.

## Required for all types
- type: pick from the table below
- title: short factual title
- summary: 1-3 concise factual bullets (≤220 chars each)
- entities: named people/systems/projects/terms grounded in the turn

## Type table — pick the closest match based on what factually happened

| type             | when to use                                                      |
|------------------|------------------------------------------------------------------|
| context          | Factual exchange, background, or orientation. No causal claim.   |
| evidence         | Observed fact, artifact, or measurement. Requires: supports_bead_ids, detail |
| data_insight     | Finding from explicit data analysis. Requires: entities, detail  |
| decision         | A choice was made. Requires: because (why it was decided)        |
| goal             | An intention with testable criteria. Requires: goal_id, success_criteria |
| hypothesis       | An untested idea. Requires: hypothesis_status (pending/validated/falsified) |
| outcome          | Something concluded. Requires: result (resolved/failed/partial/confirmed/abandoned), linked_bead_id |
| lesson           | A generalization derived from experience. Requires: because, entities, (supporting_facts or evidence_refs) |
| precedent        | A reusable if-then rule. Requires: condition, action             |
| design_principle | An architectural rule. Requires: because                        |
| reflection       | Explicit retrospective analysis. Requires: reflection_type (misjudgment/overfitted_pattern/meta_analysis/pattern_recognition) — system may also assign this automatically |
| tool_call        | A tool was invoked. Requires: tool or capability                 |
| blocked          | Attempted and halted by external constraint. Requires: blocked_by_description |
| incident         | Unplanned system/behavior failure. Requires: incident_id, severity (low/medium/high/critical) |

## Modifier fields (add when applicable to any type above)

| field             | when to use                                                      |
|-------------------|------------------------------------------------------------------|
| revises_bead_id   | This bead overrides a prior bead — set to that bead's id        |
| revision_type     | Required with revises_bead_id: reversal (changed direction) or correction (was wrong) |

## Optional but encouraged
- because / supporting_facts / evidence_refs
- detail
- state_change
- effective_from / effective_to / observed_at
- associations to visible prior beads when justified by evidence

## Quality rules
- Every bead is indexed. Write entities and supporting_facts for durable recall.
- Do not invent facts, evidence refs, dates, or associations.
- session_start, session_end, checkpoint are system-assigned — do not author these.
"""


def agent_authored_bead_spec() -> str:
    """Return adapter-consumable instructions for primary-agent bead authorship."""

    return BEAD_AUTHORING_SPEC


__all__ = ["BEAD_AUTHORING_SPEC", "agent_authored_bead_spec"]
