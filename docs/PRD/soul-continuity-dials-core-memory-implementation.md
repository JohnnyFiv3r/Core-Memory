# SOUL Continuity Dials — Core Memory Backend Support

**Status:** Implemented

## Summary

Host applications may render three read-only SOUL continuity dials:
light-cone breadth, observed-vs-endorsed divergence, and
persistent-tension set. Core Memory should provide stable read surfaces and
evidence breakdowns for those dials without treating measurements as evidence,
altering beads, changing association truth, or auto-applying SOUL revisions.

Current Core Memory exposes SOUL files, goal lifecycle reads, worldline and
storyline projections, Dreamer geometry, myelination reports, bead-scoped
assembly depth, identity/value candidates, tension candidates, and a normalized
summary output for the three dials. The summary endpoint is read-only
measurement infrastructure; it does not create evidence, mutate memory, or apply
SOUL revisions.

## Current Truth

- `GET /v1/soul/files/{file_name}` exposes `GOALS.md`, `WORLDLINES.md`,
  `TENSIONS.md`, and `IDENTITY.md`.
- `GET /v1/soul/goals` exposes Goal Beads plus lifecycle state.
- `GET /v1/memory/projection/worldlines` and
  `GET /v1/memory/projection/storylines` expose trajectory projections.
- `GET /v1/dreamer/geometry` exposes Dreamer geometry with assembly-depth
  metadata where present.
- `GET /v1/myelination/manifest` and `/v1/myelination/report` expose
  reinforcement/decay observability.
- `GET /v1/soul/summary?subject=` exposes read-only continuity measurements
  for light-cone breadth, observed-vs-endorsed divergence, and persistent
  tensions.
- `assembly_depth_report.v1` exists, but the implementation is bead-targeted;
  non-bead targets such as storylines, tensions, identity traits, and values are
  explicitly out of scope today.
- Observed-vs-endorsed divergence is exposed through the summary endpoint using
  identity/value candidates and deterministic projections over endorsed identity
  entries and observed behavior.
- Persistent tensions are exposed through the summary endpoint using SOUL
  tension entries, pending tension candidates, storyline-computed tensions, and
  goal-conflict detections. The summary includes recurrence/churn fields where
  history is available and explicit limitations where it is not.

## Shipped Read Surface

Core Memory exposes one read-only summary endpoint:

`GET /v1/soul/summary?root=&subject=self`

The response should be deterministic for a fixed store state and should include:

```json
{
  "schema": "soul_summary.v1",
  "subject": "self",
  "generated_at": "iso-8601",
  "measurements_are_evidence": false,
  "light_cone_breadth": {
    "status": "complete|partial|unavailable",
    "light_cone_index": 0.0,
    "spatial_scope_count": 0,
    "temporal_horizon_days_p90": null,
    "worldline_span_days_p90": null,
    "storyline_span_days_p90": null,
    "binding_mass": 0.0,
    "breakdown": [],
    "limitations": []
  },
  "observed_endorsed_divergence": {
    "status": "complete|partial|unavailable",
    "divergence_index": 0.0,
    "positive_observed_not_endorsed": [],
    "negative_endorsed_not_observed": [],
    "limitations": []
  },
  "persistent_tensions": {
    "status": "complete|partial|unavailable",
    "active_load": 0.0,
    "persistence_qualified_count": 0,
    "new_tension_rate": null,
    "resolution_rate": null,
    "churn": null,
    "tensions": [],
    "limitations": []
  }
}
```

Every item in a breakdown array must carry evidence identifiers or source
references sufficient for an integration UI to explain where the number came
from. Missing or unavailable metrics should return `status: "partial"` with
limitations, not silently zero.

## Dial 1: Light-Cone Breadth

The implementation provides Core Memory support for the light-cone inputs host
applications cannot derive reliably today:

- Extend assembly-depth reporting beyond bead targets or add a sibling
  read-side projection for non-bead targets:
  - storylines,
  - active tensions,
  - identity traits,
  - endorsed values.
- Add `storyline_span` as a computed factor for storyline/non-bead assembly
  depth. It should measure temporal reach across member beads/events, not
  candidate age.
- Extract endorsed goal horizon data from Goal Beads and lifecycle state:
  - subject scope,
  - target-state time horizon when present,
  - endorsed/active lifecycle state,
  - evidence refs for the goal source.
- Compute `binding_mass` from causal dependency count, assembly depth,
  myelinated path support, and supersession survival. If any component is
  missing, report a partial binding mass with limitations.
- Do not let unendorsed aspirational goals inflate horizon metrics. Candidate
  goals may appear in the breakdown but must not contribute to the primary
  horizon score.
- Breakdown rows may include read-side proxy projections for `storyline`,
  `tension`, and `identity_entry` non-bead targets. These rows explain breadth
  and binding measurements only; they are not graph edges, SOUL approvals, or
  evidence. Tension and identity rows must explicitly avoid contributing to the
  endorsed-goal horizon.

Acceptance criteria:

- A store with endorsed goals, worldlines, and assembly-depth reports returns a
  non-empty light-cone breakdown.
- A store with only candidate goals returns partial status and does not report
  an inflated endorsed horizon.
- A store without non-bead assembly support returns partial status with an
  explicit limitation instead of zeroing `binding_mass`.
- Endorsed identity entries and active/persistent tensions can contribute
  non-bead binding mass without enqueueing Dreamer candidates or mutating SOUL.

## Dial 2: Observed-vs-Endorsed Divergence

The implementation provides a signed divergence summary over `IDENTITY.md`,
behavior beads, and Dreamer identity/value candidates:

- Positive divergence means observed-supported behavior is not yet endorsed in
  `IDENTITY.md`.
- Negative divergence means an endorsed identity/value entry lacks behavioral
  support or is contradicted by current behavior.
- Preserve candidate status. Dreamer findings remain hypotheses until reviewed
  and accepted through governed SOUL workflows.
- Return separate arrays for positive and negative divergence; do not collapse
  them into a single absolute score because the two signs mean different things.
- Include supporting bead IDs, source revision IDs, candidate IDs, session/source
  counts, and confidence/grounding metadata when available.
- Expose this via the summary endpoint rather than documenting a non-existent
  `SOUL_SUMMARY` field.

Acceptance criteria:

- Existing `identity_divergence_candidate` rows appear in the negative
  divergence breakdown.
- Existing `value_candidate` rows appear in the positive divergence breakdown.
- Endorsed identity entries with matching current behavior do not produce a
  negative divergence item.
- The endpoint never creates, approves, applies, or rejects SOUL revisions.

## Dial 3: Persistent-Tension Set

The implementation provides recurrence and churn qualification for tensions:

- Normalize tension sources across:
  - `TENSIONS.md` entries,
  - pending/accepted `tension_candidate` rows,
  - Storylines computed tensions,
  - active goal-conflict detections.
- Add persistence qualification that counts recurrence only across meaningful
  separation:
  - distinct sessions,
  - distinct source objects,
  - separated observation periods,
  - or multiple participants/actors when available.
- Exclude duplicated ingests, same-session repetition, and single-source spikes
  from persistence-qualified counts.
- Compute per-tension:
  - recurrence count,
  - sessions/periods spanned,
  - assembly-depth weight,
  - age,
  - status (`active`, `candidate`, `resolved`, `superseded`),
  - related goals/worldlines/identity entries,
  - evidence refs.
- Compute aggregate active load, persistence-qualified count, new tension rate,
  resolution rate, and churn. If history is insufficient, return `null` for rate
  fields with a limitation.
- Keep tension candidates as proposals. The summary must not promote a tension
  into SOUL or active graph truth.

Acceptance criteria:

- A repeated tension across separated sessions qualifies as persistent.
- A duplicated single-session tension does not qualify as persistent.
- Resolved tensions contribute to churn/resolution metrics but not active load.
- Pending candidates are visible but clearly marked as candidate/proposal state.

## Testing

- Unit tests cover `GET /v1/soul/summary` empty, partial, and populated states.
- Fixture tests cover each dial:
  - endorsed goals and worldline span,
  - positive and negative identity divergence,
  - persistent versus duplicated tensions.
- Regression tests assert summary generation does not mutate beads,
  associations, SOUL revisions, Dreamer candidate statuses, myelination state, or
  lifecycle files.
- Public surface docs cover the endpoint and explicitly state that continuity
  dials are read-side measurements, not evidence or governance actions.

## Non-Goals

- Do not implement host-application UI rendering in Core Memory.
- Do not auto-apply SOUL updates from divergence or tension metrics.
- Do not use dial values for retrieval ranking, backbone derivation, association
  truth, or myelination rewards.
- Do not expose measurements without itemized evidence and limitations.
