# PRD: Dreamer v2 — Continuity Observer

Status: Partially shipped (storyline overlay slice); remainder Proposed
Depends on: worldline derivation (shipped), dreamer candidate queue (shipped),
myelination v2 / continuity manifest (`myelination-v2-continuity-strength.md`)

---

## Update — Storylines rescope (shipped)

The narrative slice shipped under the **Storyline** product framing:
`storyline = worldline backbone + interpretive overlay`. Naming change from
this PRD's draft: overlay records are **`storyline_overlay.v1`**, not
"observations" — the grounded `structured-observation` ingest type is a
different, factual thing and the vocabulary must never blur interpretation
into fact.

Shipped:
- `schema/storyline_overlay.py` — gate (untraceable ⇒ rejected), versioning
  via `supersedes_overlay_id`, falsifiability via
  `expected_revision_triggers`.
- `runtime/dreamer/convergence.py` — deterministic worldline-convergence
  detector (Slice A); threshold-gated `narrative_candidate` emission, wired
  into the `dreamer-run` job. Overlays are earned, never ambient.
- Accept-path materialisation to append-only `.beads/overlays.jsonl`
  (observer contract: one overlay record, zero beads/associations/claims).
- `graph/storylines.py` + `core_memory.derive_storylines` +
  `GET /v1/memory/projection/storylines` — backbone + overlays + computed
  tensions (competing overlays, claim-slot conflicts).
- One-way invariant under test: backbone derivation byte-identical with and
  without overlays present; backbone modules grep-guarded against reading
  overlay records.

Still open from this PRD: attractor detection over continuity depth
(needs myelination-v2 Slice B), value-pattern families, LLM statement
refinement, revision-trigger staleness checks at flush, and the seeded
recall/precision benchmark.

---

## Problem

Dreamer today proposes **pairwise micro-hypotheses** (association,
contradiction, entity-merge, retrieval-value candidates) over individual
beads. That is necessary but it cannot see the structures the system now
materialises: worldlines, convergence regions, continuity density. The
external self-model — which by the agreed boundary lives *outside* the graph
— needs **traceable observations about continuity** ("potential goal",
"potential value", "potential narrative", "potential worldview", "potential
attractor"), each grounded in the worldlines and beads that produced it.

The contract stays exactly as it is: **Dreamer observes; it never creates
truth.** Observations are reviewable proposals, not facts.

## Goals

1. **Macro-structure inputs.** Dreamer analysis consumes the worldline
   projection (`derive_worldlines`), worldline membership counts, the
   continuity manifest (depth), edge-lifecycle stats, and contradiction
   pressure — not just raw bead pairs.
2. **New observation families** (candidate queue, same decide flow):
   - `worldline_convergence_candidate` — N distinct worldlines repeatedly
     intersecting in the same beads/regions (membership co-occurrence).
   - `attractor_candidate` — a region of sustained high continuity depth that
     keeps acquiring new edges across sessions.
   - `narrative_candidate` — an ordered thread across converging worldlines
     (sequence of events that reads as one storyline).
   - `value_candidate` — a recurring decision pattern across goal worldlines
     (same trade-off resolved the same way ≥ k times).
3. **Observation schema** (`observation.v1`): kind, statement (one sentence),
   `supporting_worldline_ids`, `supporting_bead_ids`, confidence, novelty,
   run metadata. **Hard rule: every observation must be traceable** — an
   observation with no supporting worldlines/beads is invalid at the schema
   gate.
4. **Observation projection.** Accepted observations surface via
   `GET /v1/memory/projection/observations` (+ public API) for the external
   self-model and the geometry UI ("Narrative Star Forge" rendering). Pending
   ones remain only in the candidate queue.

## Non-goals

- Synthesizing identity/SOUL.md content (external consumer reads accepted
  observations; synthesis happens outside the graph).
- Auto-acceptance of any observation family — explicit decide remains
  mandatory (`decide_dreamer_candidate`).
- Real-time operation: Dreamer remains offline/background
  (`dreamer-run` async job kind), never on the turn critical path.
- Prediction/forecasting (external predictive layer).

## Design

### Slice A — convergence detector
- `runtime/dreamer/convergence.py`: build worldline×bead incidence from
  `derive_worldlines`; score bead regions by distinct-worldline count and
  kind diversity (claim+entity+goal intersecting > 3 entity threads alone);
  emit `worldline_convergence_candidate` rows above threshold.

### Slice B — attractor + narrative detection
- Attractors: cluster high-`continuity_depth` beads connected by
  strong active edges; require depth persistence across ≥ 2 flush cycles
  (no single-session attractors).
- Narratives: time-ordered walk along converging worldlines; candidate
  carries the ordered bead sequence as its trace.

### Slice C — value patterns
- Over goal worldlines: group resolved goals by shared tags/entities;
  detect repeated outcome-decision shapes; emit `value_candidate` with the
  supporting goal worldlines.

### Slice D — observation records + projection
- Accepted candidates of the new families materialise as observation records
  (append-only `.beads/observations.jsonl`, `observation.v1`), never as
  claims or associations. Projection route + public API reader.
- Geometry export (myelination-v2 Slice C) gains an optional
  `include_observations` flag.

### Slice E — evaluation
- Extend `runtime/dreamer/eval.py`: traceability completeness (100%
  required), reviewer acceptance rate per family, novelty distribution,
  and a seeded-fixture benchmark (synthetic store with planted convergence —
  detector must find it, and must stay silent on a shuffled control).

## Acceptance criteria

- Every emitted observation candidate validates against `observation.v1`
  with non-empty supporting worldlines and beads.
- Zero writes to claims/associations/beads from any new family — observer
  contract enforced by tests.
- Planted-fixture benchmark: convergence and attractor detectors achieve
  recall ≥ 0.9 on planted structures, zero emissions on shuffled controls.
- Dreamer runtime stays off the turn critical path (async job only).
- `docs/dreamer_contract.md` updated in the same PR — observation families,
  schema, and the traceability rule.

## Test plan

- Unit: incidence builder, thresholds, schema gate (rejects untraceable
  observations), per-family emitters.
- Integration: dreamer-run job → candidates queued → decide → observation
  record → projection route serves it; rejected candidates never project.
- Property: shuffled-store control emits no convergence/attractor candidates.
