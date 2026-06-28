# PRD: Graph Geometry Dynamics — Health, Energy, and Cone-Bounded Retrieval

**Status:** Draft v1
**Owner area:** `runtime/dreamer/geometry.py`, `graph/` (traversal, edge_weights),
`runtime/dreamer/assembly_depth.py`, the dreamer-run / health background job
**Builds on:** `docs/reports/emergent-geometry-substrate.md` (the metric/mass/well/curvature
thesis — mostly unimplemented), `myelination-reinforcement.md` (edge strength),
`recall-effort-tiers-and-traversal.md` (the `trace` cone / `g+h`),
`storylines-structural-equivalence.md` (community/role structure)
**Source research:** Quantum Graphity and its *classically computable* derivatives
(Trugenberger combinatorial quantum gravity, discrete Ollivier/Forman-Ricci flow,
CDT spectral dimension, Wolfram-model ball-growth, disordered locality, Lieb-Robinson cones).

---

## 0. Framing & honest scope (read first)

Quantum Graphity (Konopka–Markopoulou–Severini, [hep-th/0611197](https://arxiv.org/abs/hep-th/0611197),
[0801.0861](https://arxiv.org/abs/0801.0861)) models *space itself* as emergent from a
dynamical graph that **cools from a dense non-local phase into a sparse, low-dimensional,
local one**. The model is *quantum* and not classically computable at scale — but its
**mathematical derivatives are fully computable on a classical weighted graph**, and that
is all this PRD uses. Core Memory's `emergent-geometry-substrate.md` already gestures at
this (metric `d`, mass `m`, potential `φ`, Ollivier-Ricci curvature, RG blocks) but **none
of it is implemented** — the doc is a thesis. This PRD makes the *computable, defensible
subset* real as a **graph-health + maintenance + retrieval** layer.

**Scope ledger — what transfers vs. what does not:**

| Construct | Status for us | Note |
|---|---|---|
| Spectral / Hausdorff dimension, Ollivier & Forman-Ricci curvature, k-core, cycle rank, Ricci-flow surgery, shortest-path cones | **Literal, runnable graph math** | exact algorithms, cited below |
| "Geometry condenses from random bits"; degree→local-speed; matter=defect | **Inspiration for heuristics** | structural analogies, NOT derivations |
| Quantum Hamiltonian ground states, superposition of graphs, Lieb-Robinson *operator* derivation, emergent U(1) gauge, dark-energy interpretation | **Does NOT transfer** | irreducibly quantum |
| Threshold/constant calibration (`v₀`, `p`, `L_min`, `θ`) | **Free hyperparameters** | physics gives the *form*, not the values |

On a messy finite memory graph, curvature/dimension are **connectivity statistics**, not
literal spacetime — useful diagnostics and regularizers, nothing more. This PRD is
"inspiration for heuristics, not derivation."

---

## 1. Goals

- A cheap **graph-health metric layer** (dimension, curvature, core/cycle structure,
  hub/valence energy, disordered-locality density) computed on the existing association
  graph and surfaced through the geometry manifest + an HTTP read.
- An **anti-hub energy/valence guardrail** that keeps the memory graph navigable
  (suppresses runaway high-degree beads) — a real pathology of growing memory graphs.
- **Structural stability signals** (k-core depth, cycle participation, betweenness) feeding
  promotion/compaction — protect the load-bearing core, peel the fragile periphery.
- A **cone-bounded retrieval** formalization (weighted shortest-path ball + degree→speed +
  shortcut tagging) that gives the `recall` `trace` tier a principled stopping rule.

## 2. Non-Goals

- No quantum simulation; no claim that the memory graph "is" spacetime.
- No change to the **one-way rule** — geometry/health metrics are a *projection*; they never
  mutate beads, claims, C/B/A, or feed backbone derivation.
- Maintenance actions **re-rank / weaken / prune weak edges**, never delete bead content,
  and never override agent-judged associations.
- Not implementing the full `emergent-geometry-substrate.md` metric `d`/mass/`φ` program —
  this PRD takes the *computable, high-value* subset; the rest stays thesis.

---

## 3. Phase 1 — Graph-health metric layer (cheap, read-only)

Compute on the active association graph (`index.json` associations, canonical
inactive-status filter `{retracted,superseded,inactive}`), in a background step of the
dreamer-run / `health-recompute` job, and fold results into the geometry manifest
(bump `dreamer_geometry_manifest.v2 → v3`) plus a health summary block.

**Per-edge — Forman-Ricci curvature** (cheapest curvature, `O(M)`, closed form):
`F(e) = 4 − deg(src) − deg(dst)` (augmented variant adds the triangle term). Negative `F`
= bridge/bottleneck edge between dense regions; positive = within-cluster. Add `curvature`
to geometry manifest **edges**. ([Sreejith et al. 1603.00386](https://arxiv.org/abs/1603.00386),
[Samal et al. Sci.Rep. 2018](https://www.nature.com/articles/s41598-018-27001-3))

**Per-node — structural load-bearing signals** (`O(M)`):
- **k-core / coreness** (Batagelj–Zaversnik peel): how deeply embedded a bead is; coreness 1
  = leaf/fragile, high = load-bearing.
- **Local cycle participation** + graph **cycle rank** `β1 = M − N + C` (the Trugenberger
  "condensation of short cycles" order parameter, made classical): cycle-rich beads are
  mutually corroborated; tree/chain beads are fragile single-thread recollections.
- Add `coreness`, `cycle_participation` to geometry manifest **nodes**.

**Graph-level scalars — emergent dimension** (sampled, cheap):
- **Hausdorff `d_H`** via BFS ball-growth `|B(r)| ~ r^{d_H}` (log-log slope over the linear
  window). ([Wolfram/Gorard ball-volume](https://content.wolfram.com/sites/13/2020/07/29-2-3.pdf))
- **Spectral-dimension flow `d_s(t)`** via lazy-walk return probability
  `P(t) = (1/N)Tr(P_lazy^t)`, `d_s(t) = −2 d log P/d log t` (Hutchinson trace estimate; lazy
  walk to kill parity; read the **plateau**, not the saturating tail). ([AJL / CDT spectral
  dimension](https://arxiv.org/abs/hep-th/0505113))
- A persistent gap `d_s ≠ d_H` flags a **fractal/disordered** region (health signal).

**Graph-level — valence/hub energy** (`O(N)`): report the anti-hub energy
`E_V = Σ_i exp[p·(deg(i) − v₀)²]` and the degree distribution as a hub-health readout
(formula from QG valence term, [0801.0861](https://arxiv.org/abs/0801.0861)).

**Disordered-locality density** (on suspect long-range / high-betweenness edges only):
per-edge **detour ratio** `R(e) = d_{G\e}(u,v) / w(e)` (shortest path with `e` removed);
high `R` = a "wormhole" shortcut. Report `ρ = (#defect edges)/(#edges)` and the
length-resolved `ρ_length(ℓ)`. ([Caravelli–Markopoulou 1201.3206](https://arxiv.org/abs/1201.3206))

**Surface:** extend `build_geometry_manifest` to attach these; add
`GET /v1/dreamer/graph-health` (or fold a `health` block into the geometry manifest, served
from disk like geometry — never recomputed on read).

---

## 4. Phase 2 — Anti-hub valence guardrail (energy acting on maintenance)

The QG **valence term** `H_V = g_V Σ_i exp[p·(v₀ − deg(i))²]` exists precisely to prevent
high-degree blow-up — a real Core Memory pathology (a few mega-connected beads/entities that
every association points to, degrading retrieval navigability).

- Define a memory energy `E_mem = g_V Σ_i e^{p(deg(i)−v₀)²} + k Σ_edges w_e − g_loop Tr(A³)`
  (anti-hub + association-bloat cost + local-triangle/corroboration reward).
- The maintenance pass uses the **gradient locally**: when a bead's degree exceeds `v₀`, the
  pass de-prioritizes/prunes its **weakest** incident associations (lowest myelination
  strength, lowest confidence) toward the target fan-out — a hub-suppression regularizer,
  computed `O(1)` per edge change. The triangle reward protects beads embedded in dense local
  cycles.
- **Guardrail:** prune/weaken weak *edges* only; never delete bead content; never drop an
  agent-judged or human-confirmed association; respect current-truth.

(Optionally, later: an **annealing-style consolidation pass** — warm exploratory rewiring →
cool to a sparse local structure — using `⟨deg⟩` and `Tr(A³)` drift as the "running hot"
trigger. Geometrogenesis framing; deferred, §7.)

---

## 5. Phase 3 — Structural stability → promotion / compaction (matter as defects)

The QG intuition "stable matter = a locally dense, cycle-rich, hard-to-remove subgraph"
maps to durable memory. Define a structural **stability/importance**:
`importance(b) = α·coreness(b) + β·cycle_weight(b) + γ·norm_betweenness(b)`
(coreness + cycle cheap/always; betweenness `O(NM)` sampled/periodic; optional persistent-H1
weight as the expensive high-fidelity variant).

- **Promotion:** high-importance load-bearing beads are promotion/retention candidates.
- **Compaction/eviction:** peel the structurally fragile first (coreness 1, no cycles, low
  betweenness) — the literal inverse of "trapped matter."
- This **complements Assembly Depth** (which measures evidence irreducibility): consider
  adding `coreness` / `cycle_participation` as new Assembly-Depth structural factors rather
  than a parallel score.
- **Guardrail:** changes priority/retention, never visibility — a directly-queried bead is
  always returned (consistent with the myelination "re-rank, never suppress" rule).

([k-core](https://hal.science/hal-00004807v2/document),
[persistence on networks](https://link.springer.com/article/10.1007/s41109-019-0179-3),
[Brandes betweenness](https://arxiv.org/abs/1802.06701))

---

## 6. Phase 4 — Cone-bounded retrieval + shortcut tagging (light cone + disordered locality)

Make causal traversal a **light-cone walk**, dovetailing with the recall-effort PRD's
`trace` tier:

- **Retrieval cone** `D_T(S)` = beads within weighted budget `T` of seeds `S`, via
  multi-source Dijkstra — a principled stopping rule for the `trace` walk instead of an
  ad-hoc max-hops constant. (Lieb-Robinson reduced to a shortest-path ball;
  [Hamma et al. 0808.2495](https://arxiv.org/abs/0808.2495).)
- **Weight edges by inverse strength** (`w = 1/strength`) so strong/dense association
  clusters propagate "faster" (degree→speed analogue, [1108.2013](https://arxiv.org/abs/1108.2013))
  — this is exactly the `g` (structural cost) term of the recall PRD's `g+h`.
- **Shortcut tagging:** a bead reached inside the cone *only* via a high-`R` defect edge is
  tagged `via_shortcut: true` so ranking knows it bypassed locality decay (it shouldn't be
  scored as metrically near). Cross-domain serendipity, surfaced honestly. These defect edges
  are the same objects as myelinated shortcuts and the storyline "earned semantic" edges.
- **Keep defect edges OUT of the metric/dimension/curvature computations** (they distort
  `d_H`, `d_s`, curvature) but **available for retrieval shortcuts** — this sharpens the
  one-way separation between interpretive/exploratory edges and the structural backbone.

---

## 7. Deferred (higher cost / research-grade)

- **Ollivier-Ricci `κ = 1 − W₁(m_x,m_y)/d(x,y)`** (optimal-transport per edge) and
  **Ricci-flow + surgery** for community/bottleneck re-segmentation — an offline batch job;
  maps onto the storyline structural-equivalence clustering.
  ([Ni et al. Sci.Rep. 2019](https://www.nature.com/articles/s41598-019-46380-9),
  [Trugenberger 1610.05934](https://arxiv.org/abs/1610.05934),
  ref impl [GraphRicciCurvature](https://graphriccicurvature.readthedocs.io/))
- **Geometrogenesis / annealing consolidation** (§4 note) as a cold-start→condensed model:
  early tenant = high-`d_s`, low-cycle random tangle → cools to low-dimensional structure;
  `d_s`/cycle-density as the objective transition detector.
- The remaining `emergent-geometry-substrate.md` program (full MI metric `d`, mass-coupled
  decay, potential `φ`, RG block-spin, entropic-time identity trajectory).

---

## 8. Cost & cadence

| Metric | Cost | Cadence |
|---|---|---|
| Forman-Ricci, coreness, cycle rank `β1`, valence energy | `O(M)` / `O(N)` | every health run (cheap) |
| Hausdorff `d_H`, spectral-dim flow `d_s(t)` (sampled) | `O(seeds·M)`, `O(t·M·probes)` | every health run (sampled) |
| Detour ratio `R(e)` (suspect edges), betweenness (sampled) | `O(M)`–`O(NM)` | periodic |
| Ollivier-Ricci `κ`, Ricci-flow surgery | OT per edge / iterative | offline batch (deferred) |

All computed **off the hot path** in the background job and **served from the persisted
manifest** — retrieval reads, never recomputes (same pattern as geometry today).

---

## 9. Guardrails

1. **One-way rule / projection-only.** Health metrics never mutate beads/claims/C-B-A; they
   are a read-side projection. Backbone derivation is byte-identical with/without them.
2. **Re-rank, never suppress.** Valence pruning and stability eviction change weight/priority
   of *edges* and retention order, never bead visibility; directly-queried beads always return.
3. **Defect edges: shortcuts yes, metric no.** Excluded from dimension/curvature; available
   to retrieval as tagged shortcuts.
4. **Hyperparameters are tuned, not inherited.** `v₀, p, k, g_loop, L_min, T, θ` come from
   eval, not from the physics.
5. **Off the hot path, sampled estimators** for `O(NM)` metrics; finite-graph caveats encoded
   (lazy walks, read the plateau, ball linear window).

---

## 10. Rollout

1. **Phase 1** — health metric layer (Forman curvature, coreness, cycle rank, `d_H`/`d_s`,
   valence energy, defect density) into the geometry manifest (v3) + `GET /v1/dreamer/graph-health`.
2. **Phase 4** — cone-bounded retrieval + shortcut tagging (lands with / feeds the recall
   `trace` tier).
3. **Phase 2** — anti-hub valence guardrail in the maintenance/pruning pass.
4. **Phase 3** — structural stability into promotion/compaction (+ optional Assembly-Depth factors).
5. **Deferred** — Ollivier-Ricci + Ricci-flow surgery; annealing consolidation; the rest of
   the substrate thesis.

---

## 11. Success criteria

- The geometry manifest carries per-edge curvature, per-node coreness/cycle participation,
  and graph-level `d_H`/`d_s`/valence-energy/defect-density — served over HTTP, recomputed
  only on the background cadence.
- Hub formation is measurably bounded: degree distribution tail shrinks after the valence
  guardrail, with **no** loss of recall on a labeled set.
- Compaction peels fragile periphery before load-bearing core (coreness/cycle/betweenness
  validated against a labeled retention set); no directly-queried bead is ever hidden.
- The `trace` cone gives a principled, tunable stopping rule; shortcut hits are tagged and
  excluded from metric computations.
- One-way-rule test stays green; no bead/claim/C-B-A mutation from any metric.

---

## 12. Honest caveat

Every signal here is real classical graph math, but its *physical* interpretation is a
borrowed analogy. We adopt the **computations** (they are cheap, local, and target genuine
memory-graph pathologies — hubs, fragility, bottlenecks, distance distortion) and explicitly
**not** the metaphysics. Treat this as a principled diagnostics/regularization layer inspired
by emergent-geometry physics, validated by retrieval/maintenance outcomes — not as a claim
that memory is spacetime.
