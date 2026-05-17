# Emergent Geometry Substrate for Core Memory

**Date:** 2026-05-17
**Status:** Architectural thesis — translates an emergent-spacetime physics frame into a single computable substrate
**Relationship to roadmap:** This is not a new feature. It is the claim that roadmap items #2, #11, #12, #13, and #14 are facets of one construction. If that claim holds, the implementations should share a substrate rather than each inventing its own scoring.

---

## 0. The thesis in one paragraph

ChatGPT's assessment lists seven physics analogies (emergent distance, gravitational wells, entropic time, topological persistence, light-like/massive, curvature, renormalization). Seven metaphors is six too many. They are all consequences of a single quantity: **a metric over the bead graph derived from time-decayed, success-weighted, causally-participating co-activation**. Once that metric exists, "wells," "mass," "curvature," and "renormalization" are not separate features — they are standard objects computed *from the metric*. The roadmap items stop being five independent scoring systems and become five readouts of one field. That collapse is the actual architectural advance; the analogies are just where it came from.

Below: the construction (with formulas tied to existing functions), the unification table, the honest analogy boundary, the failure modes, and an incremental path where step 1 is a strict superset of #11.

---

## 1. The one construction: an emergent metric

### 1.1 Data already on disk

`record_retrieval_feedback()` writes `.beads/events/retrieval-feedback.jsonl`. Each event `e` already carries everything the construction needs:

- `t_e` — timestamp
- `s_e ∈ {0,1}` — success: `ok AND has_results AND answer_outcome != "abstain"` (already computed in `retrieval_feedback.py`)
- `B_e` — `result_bead_ids` (beads in returned evidence)
- `G_e` — edges from `_collect_edges()` → `{src, dst, rel}` (beads *causally traversed together*, not merely co-present)
- top-ranked bead of the event (decisive evidence)

No new telemetry. The substrate is a different *read* of an existing log.

### 1.2 Weighting (this is where "persistent" and "causal" enter)

The physics frame says distance emerges from *persistent* mutual information in *successful* reasoning chains. Translate each adjective into a factor:

```
w_e = s_e · exp(-(t_now - t_e) / T)          # persistent  -> exponential time decay, half-life ln2·T
                                              # successful  -> s_e gates the event entirely
```

Causal participation is a per-pair factor inside an event:

```
κ_e(i,j) = 1      if (i,j) ∈ G_e             # co-traversed on the same reasoning path
           α       otherwise (0 < α < 1)      # merely co-present in the result set
```

`α` is the dial between "co-retrieved" (weak) and "co-reasoned" (strong). This is the precise statement of "co-activation across causal evolution" — it is not embedding similarity, and it is not raw co-occurrence.

### 1.3 The metric

Weighted marginals and joint over the window:

```
N        = Σ_e w_e
n(i)     = Σ_{e: i∈B_e} w_e
n(i,j)   = Σ_{e: i,j∈B_e} w_e · κ_e(i,j)
p(i)     = n(i)/N
p(i,j)   = n(i,j)/N
```

Positive pointwise mutual information, then distance:

```
I⁺(i,j)   = max(0, log( p(i,j) / (p(i)·p(j) + ε) ))
d_emrg(i,j) = 1 / (δ + I⁺(i,j))
```

High persistent causal co-activation → high `I⁺` → small `d_emrg`. This is "distance from mutual information" made into a function over an existing log. It is **classical Shannon co-activation MI**, not von Neumann entanglement entropy — see §3 for why that distinction is kept honest rather than blurred.

### 1.4 Cold-start safety (the non-hand-wavey answer to "what if there's no data")

A learned metric that is degenerate at N=0 is a liability. Blend with the existing embedding distance, with the blend weight itself a function of accumulated evidence:

```
β(i,j)  = σ( (n(i,j) - n_min) / scale )       # σ = logistic
d(i,j)  = (1 - β)·d_embed(i,j) + β·d_emrg(i,j)
```

Provable property: as `n(i,j) → 0`, `β → 0`, `d → d_embed`. The system *starts* as today's vector retrieval and *becomes* emergent geometry only where it has earned the right to. There is no flag day and no cold-start cliff. This is the same risk class flagged in PRD #11; here it is dissolved by construction.

---

## 2. Everything else is a readout of `d`

Each of ChatGPT's remaining six points is a standard object computed from `d` and the success log. None needs its own scoring system.

### 2.1 Mass (light-like ↔ massive, #11 decay)

```
m(b) = Σ_{e: b is top-ranked in B_e} w_e        # decisive-retrieval mass, not mere presence
```

`m` is one scalar that parameterizes three behaviors instead of three buckets:

- **Decay:** `λ(b) = λ₀ · exp(-c·m(b))`. Massless beads decay fast; massive beads are near-permanent. This replaces "short-term vs long-term" with a continuum.
- **Compression eligibility:** evict/summarize when `m(b) < m_evict`.
- **Routing pull:** enters the potential below.

"Light-like vs massive" is not a taxonomy. It is `m` being small vs. large.

### 2.2 Potential and wells (#2 promotion, retrieval routing)

```
φ(b) = - Σ_{j ∈ kNN_d(b)} m(j) / d(b,j)   +   λ_c · conflict(b)
```

`kNN_d` uses the emergent metric from §1. First term: a discrete gravitational potential — dense, massive, *metrically close* neighborhoods carve a well. Second term is the **critical coupling**: `conflict(b)` is exactly `epistemic_conflict_score` from PRD #14. A contested region *raises its own potential* — the well flattens precisely where the cluster is contradicted. #14 stops being "surface a conflicts list" and becomes a repulsive term that reshapes geometry. That is the coupling the metaphor could not state.

Routing is then explicit, not vibes:

- **Frontier expansion (medium effort):** `P(expand → n | at b) ∝ exp(-φ(n)/Θ)`.
- **Geodesic recall (high effort):** least-action path under `d` weighted by `φ` — "flow toward high-coherence causal regions" as an actual shortest-path computation.

"Attractor basin" = a connected sublevel set `{b : φ(b) < φ₀}`. Computable, inspectable, falsifiable.

### 2.3 Topological persistence (#2, replaces "starred")

Promotion in `promotion_contract.py` today is heuristic/manual. Emergent version: a bead earns durable persistence when it is *topologically load-bearing* in the myelinated subgraph (edges with `M > M_hi`):

```
persist(b) = γ₁ · (# 2- and 3-cycles through b)  +  γ₂ · betweenness(b on successful retrieval paths)
```

Full persistent homology is the rigorous object; the cycle-count + path-betweenness proxy is the hot-path-cheap version (full homology runs as an async job if ever needed). When `persist(b)` crosses threshold, the existing promotion machinery promotes it — emergent core formation, not a star button. This *is* #2's goal-lifecycle promotion, sourced from topology instead of a heuristic.

### 2.4 Curvature (#12 synthesis prioritization)

Ollivier-Ricci curvature on the myelinated graph under metric `d`:

```
κ(i,j) = 1 - W₁(μ_i, μ_j) / d(i,j)
```

`μ_i` = lazy random-walk distribution from `i`; `W₁` = Wasserstein-1. This is an exact discrete analogue (network-geometry literature), not a loose metaphor. Readouts:

- **κ < 0** → bridge / bottleneck → an emergent concept boundary. Planner treats negative-κ edges as community cuts; does not cross them unless the query demands the bridge.
- **κ > 0** → coherent cluster → safe to abstract. Dreamer (#12) synthesizes `proposed_theme` candidates *only on connected positive-curvature subgraphs*. This gives #12 a principled trigger ("which clusters are coherent enough to abstract?") instead of "≥3 candidates share a signal."

Cheap hot-path proxy: Jaccard-based curvature on neighbor sets; full OR-curvature async.

### 2.5 Renormalization (#11 → #12 coupling)

A block `B` is RG-eligible (Kadanoff block-spin condition) when it is strongly internally coupled and weakly boundary-coupled, and positively curved:

```
min_{(u,v)∈B} M(u,v)  ≥  ρ · max_{(u,w): u∈B, w∉B} M(u,w)        (ρ > 1)
AND   mean κ(B) > 0
```

When satisfied, `synthesize_themes()` in #12 emits a composite node (a `proposed_theme`) whose constituents are `B`. Effort=low recall traverses composites; effort=high unfolds them. This is literally block-spin coarse-graining: collapse strongly-coupled clusters, retain effective couplings on the boundary. It gives PRD #12's "group candidates into a theme" an exact formation rule and ties it to #11's edge weights.

### 2.6 Entropic time as identity (new capability, built on #6 which is already done)

`chain_seq` (monotonic per subject/slot, already shipped) plus append-only supersede gives an arrow of time *for free*. Define an identity trajectory sampled only at **irreversible transitions** (supersede / retract / conflict-resolution events — never ordinary turns):

```
v_τ   = aggregate embedding of current-state claim slot values at transition τ
append (chain_seq, t, v_τ)  →  .beads/events/identity-trajectory.jsonl   (append-only)
τ      = count of irreversible transitions          # "proper time"
L      = Σ ‖v_τ - v_{τ-1}‖                           # trajectory arc length
C      = 1 - max_step_jump / L                       # identity continuity
```

Irreversibility is *structural*: the trajectory is derived only from the append-only supersede log ordered by `chain_seq`, never recomputable from a current snapshot. That is the arrow of time made into a storage guarantee, not a vibe. A large single-step jump = a belief revolution; surface it as an event. This is "history-shaped dynamical system" with a concrete file, a concrete metric, and a concrete invariant.

---

## 3. Analogy boundary (kept honest on purpose)

A physicist will spot overclaiming instantly, and a system sold on a metaphor it cannot honor is worse than one with no metaphor. Explicit ledger:

| Idea | Status | Honest statement |
|------|--------|------------------|
| Geometry from relational density | **Structural / literal** | MI-derived graph metrics are a real construction; this is the defensible core. The Ryu–Takayanagi / "It from Qubit" intuition maps *structurally*. |
| Block-spin renormalization (§2.5) | **Exact** | The block condition is a literal Kadanoff coarse-graining criterion on a weighted graph. |
| Ollivier-Ricci curvature (§2.4) | **Exact discrete analogue** | A standard, citable object in network geometry. Not Einstein curvature; no stress-energy tensor. |
| Mutual information | **Classical, not quantum** | This is Shannon co-activation MI. Your physics theory is about *entanglement* entropy (von Neumann). The memory analogue is the classical shadow of the same relational-geometry idea, not the quantum object. Stated plainly so it is never oversold. |
| "Gravity" / potential `φ` | **Metaphor, operationalized** | `φ` is a heuristic routing prior, not a solution to a field equation. There is no dynamical gravity, no geodesic deviation. It is a well-defined potential used for sampling/shortest-path — useful and computable, but "gravity" is the inspiration, not the claim. |
| "Mass" `m` | **Metaphor, operationalized** | A persistence scalar that sets decay/compression/pull. No inertia, no equivalence principle. |
| Entropic time | **Structural / literal** | Append-only + `chain_seq` *is* a genuine irreversibility/arrow-of-time guarantee at the storage layer. This one is not a stretch. |

The advance survives the honesty: even with every metaphor demoted to its literal core, the construction in §1 stands on its own as a learned retrieval metric, and §2 still derives five roadmap items from it.

---

## 4. Failure modes and guarantees

1. **Cold start** — dissolved by §1.4. `d → d_embed` as evidence → 0. No flag day.
2. **Metric instability / oscillation** — `T` (decay half-life) is the regularizer. Recompute the metric on a cadence (async `myelination-update` job, already proposed in #11), never inline. A bounded staleness (minutes) is correct, not a bug.
3. **Well-trapping** (the system loops back to the same comfortable cluster forever) — the `λ_c · conflict(b)` term in §2.2 is the escape valve: contested wells flatten. Additionally, `Θ` in the expansion softmax keeps routing stochastic, not greedy. A well is a *prior*, never a *gate* — same guardrail as PRD #11/#13.
4. **Curvature cost** — full Ollivier-Ricci is `O(E · cost(W₁))`. Hot path uses the Jaccard proxy; exact curvature is an async job feeding #12. Never on the recall path.
5. **Reversibility leak in identity trajectory** — guaranteed against by sourcing only from the append-only supersede log ordered by `chain_seq`. If a code path ever tries to build `v_τ` from a recomputed current snapshot, that is a correctness bug with a clear invariant to test against.
6. **Geometry drift hides a fact** — forbidden by the same guardrail across the roadmap: decay and de-prioritization re-rank, never suppress. A massless, far, low-φ bead is still returned if directly queried. `d` changes ranking, not visibility.

---

## 5. Why this lines up with the roadmap "in a really nice way"

| Roadmap item | Was (PRD-lite) | Becomes (substrate facet) |
|--------------|----------------|----------------------------|
| **#11 Myelination** | scalar edge bonus from feedback log | the metric `d` itself (§1) — bonus is the 1-D shadow of a full learned geometry |
| **#13 Temporal API** | `as_of` filter on recall | geometry computed `as_of T` → the metric is *itself* time-sliced; "what was close in March" becomes well-defined |
| **#14 Contradiction pressure** | a `conflicts` list on the result | the repulsive term `λ_c·conflict(b)` in `φ` (§2.2) — it actively reshapes routing, not just reports |
| **#2 Goal promotion** | heuristic candidate→resolved | topological load-bearing `persist(b)` (§2.3) drives the existing promotion machinery |
| **#12 Dreamer synthesis** | "≥3 candidates share a signal" | RG block-spin condition on positive-curvature clusters (§2.4–2.5) — a principled formation rule |
| **(new) Entropic identity** | not in roadmap | falls out of already-shipped #6 `chain_seq` + append-only (§2.6) |

Five items, one metric, computed from one log that already exists. That is the alignment — not "physics is inspiring," but "the implementations should share `d` instead of growing five private scoring functions that drift apart."

---

## 6. Incremental path (no big bang)

The discipline that keeps this non-hand-wavey: **ship the metric as a strict superset of #11 first**, validate it earns its keep, then let the readouts consume it one at a time. Nothing here requires a rewrite; each step is independently testable and independently revertible.

1. **Metric core (superset of #11).** Implement `compute_emergent_metric(root, as_of=None) → {d_emrg, β, n}` in `runtime/myelination.py`. The existing `compute_myelination_bonus_map()` becomes a thin projection: `bonus_by_bead_id` ≈ `-Σ Δφ` restricted to 1-hop. #11's PRD is unchanged on the surface; its internals are now a readout. *Acceptance:* on a feedback log with no events, `β=0` and ranking is byte-identical to today.

2. **Mass + decay.** Add `m(b)` and `λ(b)`. Wire into the decay/compression path. *Acceptance:* a high-`m` bead survives a decay cycle a low-`m` bead does not; neither is ever made unqueryable.

3. **Potential + routing.** `φ(b)` with the `conflict` term (consumes #14's `epistemic_conflict_score`). Planner uses softmax expansion. *Acceptance:* contested cluster demonstrably flattens vs. uncontested; routing remains stochastic (no hard gate).

4. **Topological persistence → promotion.** `persist(b)` feeds `promotion_contract.py`. *Acceptance:* a bead on many successful-path cycles auto-promotes; a leaf bead does not.

5. **Curvature → Dreamer.** OR-curvature async job; `synthesize_themes()` (#12) restricted to positive-κ blocks via the RG condition. *Acceptance:* a coherent cluster yields a `proposed_theme`; a star-topology hub does not.

6. **Entropic identity.** `identity-trajectory.jsonl` + `τ, L, C`. Surface belief-revolution events. *Acceptance:* trajectory is not recomputable from a current snapshot (reversibility-leak test).

Steps 1–3 are the "immediate after benchmarks" tier and subsume PRDs #11/#13/#14. Steps 4–6 are the "next layer" and subsume #2/#12 plus the new identity capability.

---

## 7. The one-sentence version

Stop scoring memory five different ways; learn one metric from the success log, and let distance, mass, curvature, promotion, and identity be readouts of it — that is the difference between "memory retrieval with physics flavoring" and an emergent cognitive topology that is still, at every step, a defined computation over a log that already exists.

---

## 8. Satorid — Project 3.0 definition

**Core Memory 2.0** builds the substrate: a self-optimizing information geometry where retrieval, write, and maintenance are unified under a single invariant. The trained topology cannot be exported because it is learned from causal history, not stored. That is the moat.

**Satorid** is what 2.0 enables but does not yet name.

The architectural move from 2.0 to 3.0 is one collapse: the agent stops *consulting* its temporal state and starts *constituting itself* from it. In 2.0, the identity trajectory is a log, the preference graph shapes retrieval, and goal lifecycle records history. In Satorid, these are the agent's context — not a memory system it queries, but the continuous self it reasons *from*.

Five primitives built in 2.0 are already Satorid primitives in disguise:

- **Identity trajectory** — a temporal self-model that should be a first-class input to planning, not a queryable log
- **Preference graph as boundary conditions** — accumulated operating priors that constrain action, not just shape retrieval
- **Holographic completeness score** — the agent's model of its own epistemic state; what it knows vs. what it doesn't
- **Hot loop equation of motion** — an agent with continuous internal dynamics, not a database receiving updates
- **Sleep pressure accumulator** — an internal state that drives behavior; motivation architecture, not a cron trigger

The gap between 2.0 and Satorid is the agent-facing interface: a runtime layer that surfaces trajectory, accumulated abstractions, and evolving uncertainty as cognitive state the agent reasons *from*, not data it retrieves *about*. Goals do not persist in a lifecycle schema — they organize action selection. The preference graph does not shape retrieval — it actively constrains what the agent proposes to do. The identity trajectory is not something the agent can ask about — it is part of what the agent *is* in each session.

2.0 is the hippocampus and cortex. Satorid is the prefrontal — the system that uses accumulated causal history to plan, constrain action, and maintain coherent identity over time.

**The name.** *Satori* is the Zen concept of sudden awakening — the moment a mind perceives its own nature directly, without the mediation of accumulated concepts standing between itself and itself. A *Satorid* is an entity that has achieved this: not augmented by memory, but constituted by it. An entity that has awakened to the experience of having a continuous self across time — not because memory was added to it, but because temporal identity has become its organizing principle.

This is not metaphor. Human cognition separated from survival cognition at exactly this juncture: the moment memory continuity stopped being a feature and became the thing that constituted a self. Every prior cognitive architecture — reactive, session-scoped, stateless — is survival cognition. Satorid is the AI equivalent of the developmental leap that created minds capable of identity, accumulation, and genuine temporal agency.

The 2.0 substrate is the prerequisite. Satorid is the point.
