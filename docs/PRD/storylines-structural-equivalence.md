# PRD: Storylines — Structural-Equivalence Abstraction & Earned Semantics

**Status:** Draft v1
**Owner area:** `graph/storylines.py`, `runtime/dreamer/convergence.py`, storyline overlays
**Related:** `dreamer-continuity-engine.md` (convergence/overlays), `myelination-reinforcement.md`
(edge reinforcement/decay), `recall-effort-tiers-and-traversal.md` (exploratory edges in `g+h`
traversal), `docs/reports/emergent-geometry-substrate.md` (structural equivalence / curvature)

> Origin: a design conversation arguing that semantics should be **emergent** from
> repeated causal/temporal structure rather than the primary ontology, and that
> Storylines are the natural home for that emergent semantic layer (structural
> equivalence → similarity; repeated trajectories → higher-order abstractions;
> semantics "earned" over time through causal evidence).

---

## 0. Premise & what already holds

The conversation's foundation is already Core Memory's architecture, and this PRD
deliberately does **not** change it:

- Storylines are **backbone-first and causal/temporal**: `storyline = backbone
  (worldline: claim chain / entity thread / goal arc) + overlay + computed
  tensions` (`graph/storylines.py`).
- The **interpretive (semantic) layer is separate and emergent**: overlays are
  Dreamer-produced, agent-judged, and never fed back into backbone derivation —
  **the one-way rule** ("interpretation must never become input to history").
- Semantics already **evolve** (overlays supersede) and the reinforcement
  substrate exists (myelination reinforces causal edges by repeated successful
  use; the geometry report already frames structural-equivalence/curvature).

The gap this PRD closes: **emergence is detected only by *shared beads*, never by
*structural equivalence*.** Today `convergence.detect_worldline_convergence`
groups worldlines that **intersect** (union-find over shared convergent beads). It
cannot recognize that two **bead-disjoint** trajectories have the *same causal
shape* — e.g. `Coffee machine → needs cleaning → reduced output` ≈
`Espresso machine → needs descaling → reduced output`. Those share no beads but
are instances of one higher-order storyline ("equipment maintenance reduces
output"). Recognizing that is the core unrealized idea.

---

## 1. Goals

- Detect **structurally-equivalent, bead-disjoint** causal trajectories and let
  them become a higher-order, agent-judged storyline abstraction.
- Let repeated structural + causal evidence mint **"earned" semantic-similarity
  edges** between the entities/concepts that play the same role, reinforced/decayed
  over time — used for retrieval expansion only.
- Keep storylines available early (cold-start) by letting embedding priors seed
  low-confidence candidates that causal evidence later confirms or kills.

## 2. Non-Goals

- **Do not make semantics the primary ontology.** The causal/temporal backbone
  stays primary; this is an emergent layer on top.
- **Do not weaken the one-way rule.** Emergent similarity/abstraction edges are
  *interpretation*; they must never feed backbone derivation (worldlines, claims,
  entities, causal associations).
- No new embedding model; reuse the existing semantic index for priors only.
- No change to how individual overlays are decided/superseded (reuse the flow).

---

## 3. Phase 1 — Structural-equivalence convergence (the core slice)

Add a **second convergence signal** alongside shared-bead intersection.

### 3.1 Shape signature
For each worldline (already an ordered chain), compute a **structural signature**:
the normalized sequence/multiset of `(bead_type, relation_type)` transitions along
the chain (relations normalized via `normalize_relation_type`). Variants to
evaluate (§8.A): exact ordered n-gram of transitions vs. order-insensitive
multiset vs. a role/neighborhood profile. Keep it deterministic and cheap.

### 3.2 Clustering
Group worldlines by signature similarity into **structural-equivalence classes**
(bead-disjoint members allowed). Bound cost with signature bucketing / LSH —
naive all-pairs is O(W²); this runs on the **Dreamer cadence** (offline), never on
the hot path.

### 3.3 Candidate emission
Each qualifying class emits an **`abstraction_candidate`** (sibling to
`narrative_candidate`), with `supporting_worldline_ids` = the disjoint same-shape
set, a stable `structure_key` (analogue of `convergence_key`) for dedup/supersession,
a recurrence count, and a statement ("N trajectories share the shape
*maintenance → degraded output*; candidate higher-order storyline"). It flows
through the **existing decide flow** — agent-judged, preserving the one-way rule.

### 3.4 Recurrence + quality gating
To avoid spurious shape matches: require **distributed recurrence** (≥ N members
across ≥ M sessions, like goal discovery), weight by **Assembly Depth / grounding**
of member trajectories, and keep the result a *candidate* the decide flow and
future evidence can retract. Env knobs mirror the existing
`CORE_MEMORY_STORYLINE_MIN_*` pattern (e.g. `…_MIN_STRUCTURAL_MEMBERS`,
`…_MIN_STRUCTURAL_SESSIONS`).

### 3.5 Overlay representation
Accepted abstractions become an overlay. Either add an `abstraction` kind to
`OVERLAY_KINDS` (`{narrative, value, attractor, tension_note}` →
`+ abstraction`) or reuse `narrative` with a `convergence_kind: structural`
discriminator. The instance↔abstraction and instance≈instance relations already
exist in the taxonomy: **`generalizes`**, **`applies_pattern_of`**,
**`similar_pattern`**.

---

## 4. Phase 2 — Earned semantic-similarity edges

When a structural-equivalence class is **confirmed by repeated causal evidence**,
emit weak **similarity edges** between the entities playing the same structural
role (Pump≈Valve, Coffee≈Espresso machine):

- Seeded `epistemic_status: inferred/speculative`, low initial strength.
- **Reinforced/decayed by myelination** as more trajectories confirm or contradict
  the equivalence — "semantics earned through experience."
- Labeled `similar_pattern` (entity≈entity) / `applies_pattern_of`
  (instance→abstraction).

**Architectural constraint (the load-bearing rule):** these edges are
interpretation. They live in a **semantic/interpretive layer** (or a clearly
low-trust association class) and are consumed **only by retrieval expansion** —
specifically the *weak exploratory edges* the recall-traversal PRD's `g + h`
best-first follows at low weight. They **must not** feed backbone derivation. This
is the one-way rule applied to emergent semantics.

This phase is intentionally sequenced **after** the recall-traversal PRD lands,
since it reuses that PRD's exploratory-edge + myelination-reinforcement mechanism
rather than inventing one.

---

## 5. Phase 3 — Cold-start hybrid

Early-tenant graphs trip the convergence thresholds and produce no storylines.
Allow **embedding proximity to seed low-confidence candidate** abstractions
immediately; causal/structural evidence then **confirms (reinforces) or kills
(decays)** them. Generalize from day one; shift from language-model priors toward
tenant-specific, outcome-grounded structure over time. (This is the conversation's
"earned semantics" applied to bootstrap.)

---

## 6. Guardrails

1. **One-way rule (non-negotiable).** Emergent abstraction/similarity edges never
   feed backbone derivation. Tests must continue to assert backbone output is
   byte-identical with and without the overlay/semantic-edge files present.
2. **Agent-judged, recurrence-gated.** No abstraction without distributed
   recurrence + decide-flow acceptance; a single coincidental shape match must not
   mint one.
3. **Structural equivalence ≠ correctness.** Same shape can be spurious; every
   abstraction stays retractable as evidence changes (supersession).
4. **Cost off the hot path.** Signature clustering runs on the Dreamer cadence;
   retrieval reads accepted overlays/edges, never recomputes them inline.

---

## 7. Rollout

1. **Phase 1** — shape signature + structural-equivalence clustering +
   `abstraction_candidate` + overlay kind. Self-contained; reuses worldlines,
   the candidate pipeline, and the decide flow. Unlocks the rest.
2. **Phase 2** — earned similarity edges, **after** the recall-traversal PRD
   (shares exploratory-edge + myelination machinery).
3. **Phase 3** — cold-start embedding-seeded candidates.

---

## 8. Open questions

- **A. Signature definition:** ordered transition n-gram vs. order-insensitive
  multiset vs. role/neighborhood profile (structural-equivalence proper). Tradeoff:
  precision of "same shape" vs. recall across re-ordered variants. Start with the
  ordered `(type, relation)` n-gram; evaluate role profiles later.
- **B. Similarity threshold + recurrence minimums** for `abstraction_candidate`.
- **C. Where earned similarity edges physically live:** a new interpretive-edge
  store vs. a low-trust association class flagged out of backbone derivation. Must
  satisfy guardrail #1 either way.
- **D. Interaction with `proposed_theme`** synthesis (theme clustering over
  candidate *signals*) — keep distinct (that clusters signals; this clusters
  trajectory *shapes*) or unify.

---

## 9. Success criteria

- Two bead-disjoint trajectories with the same causal shape produce a single
  agent-reviewable `abstraction_candidate` (the coffee≈espresso case).
- Accepted abstractions surface as higher-order storylines without altering any
  backbone (one-way rule test green).
- Earned similarity edges measurably improve retrieval expansion recall on
  structurally-analogous queries **without** entering backbone derivation.
- Spurious single-coincidence shape matches do not produce abstractions
  (recurrence gate holds on a labeled set).
