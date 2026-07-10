"""Prompt-facing instructions generated from the authored-update contract."""

from __future__ import annotations

from .agent_authored_updates import (
    AGENT_AUTHORED_UPDATES_V1,
    AGENT_AUTHORED_V1_BEAD_FIELDS,
    authored_contract_snapshot,
)

_TYPE_TABLE = """| type             | when to use                                                      |
|------------------|------------------------------------------------------------------|
| context          | Factual exchange, background, or orientation. No causal claim.   |
| evidence         | Observed fact, artifact, or measurement.                          |
| data_insight     | Finding from explicit data analysis.                              |
| decision         | A choice was made. Requires grounded because.                     |
| goal             | An intention with testable criteria.                              |
| hypothesis       | An untested idea.                                                  |
| outcome          | Something concluded.                                               |
| lesson           | A generalization grounded in experience.                           |
| precedent        | A reusable if-then rule.                                            |
| design_principle | An architectural rule. Requires grounded because.                  |
| reflection       | Explicit retrospective analysis.                                   |
| tool_call        | A tool was invoked.                                                 |
| blocked          | Attempted and halted by an external constraint.                    |
| incident         | Unplanned system or behavior failure.                              |"""


def build_agent_authoring_spec() -> str:
    snapshot = authored_contract_snapshot()
    required = ", ".join(snapshot["required_bead_fields"])
    all_fields = ", ".join(sorted(AGENT_AUTHORED_V1_BEAD_FIELDS))
    return f"""Core Memory agent-authored turn-memory contract

Return one JSON object conforming to `{AGENT_AUTHORED_UPDATES_V1}`. Core Memory
provides typed guardrails and persists valid authored meaning; you lead every
semantic decision. Submit the object through the host's dedicated memory
authoring channel; never replace or expose the user-facing assistant answer with
this JSON.

## Required lifecycle
- Attempt exactly one `beads_create` row with `creation_role="current_turn"` for
  every finalized top-level turn. Include the finalized turn in `source_turn_ids`.
- You may add zero to two `creation_role="derived"` companion rows when one turn
  produces additional durable memories. Each must set
  `derived_from_bead_ids=["$current_turn"]`.
- When a turn has little durable meaning, still write a thin current-turn bead
  with `retrieval_eligible=false`; do not invent richness.
- Always return top-level `schema_version`, `beads_create`, `associations`, and
  `reviewed_beads` arrays.

## Required bead fields
{required}

`entities` is a typed list and may be empty when no grounded entity exists.
`retrieval_eligible` is always an explicit agent decision. Set it true only when
all of these are present: a non-generic title, a useful `retrieval_title`, at
least one concrete `retrieval_fact`, and at least one grounded signal such as
`because`, `supporting_facts`, `evidence_refs`, `state_change`, or supersession.

## Type table

{_TYPE_TABLE}

Populate type-specific fields whenever applicable: evidence uses
`supports_bead_ids`; goals use `goal_id` and `success_criteria`; outcomes use
`result` and `linked_bead_id`; precedents use `condition` and `action`;
hypotheses use `hypothesis_status`; reflections use `reflection_type`; tool calls
use `tool` or `capability`; blocked memories use `blocked_by_description`; and
incidents use `incident_id` and `severity`.

## Full semantic field inventory
Evaluate every optional field for applicability and fill it only when grounded:
{all_fields}

Claims and claim updates remain embedded on their bead. Use visible prior bead
IDs for justified associations, with relation, direction, evidence-bearing
reason text, and confidence. When promotion review is supported, return an
explicit `reviewed_beads` judgment for visible session beads.

Never invent facts, entities, dates, evidence, claims, semantic keys,
associations, or promotion reasons. Unknown fields are never stored.
"""


BEAD_AUTHORING_SPEC = build_agent_authoring_spec()

__all__ = ["BEAD_AUTHORING_SPEC", "build_agent_authoring_spec"]
