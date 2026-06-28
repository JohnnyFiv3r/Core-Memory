# PRD: Relational Constraint Rules (RCO → memory-graph write rules)

**Status:** Draft v1
**Owner area:** `runtime/observability/myelination*.py`, the association write/crawl path
(`association/`, `runtime/associations/`), `graph/edge_weights.py`
**Builds on:** `myelination-reinforcement.md` (edge strength), `storylines-structural-equivalence.md`
(earned semantics), `graph-geometry-dynamics.md` (anti-hub navigability)
**Origin:** the *Relational Constraint Order* (RCO) extension of Quantum Graphity. This PRD
takes **only** the constraint terms that map to real memory-graph behavior and implements
them as **cheap local rules on the write/maintenance path** — explicitly NOT as a Hamiltonian
minimized by annealing.

---

## 0. Framing & scope (read first)

RCO proposes adding constraint terms (monogamy, capacity, law-compatibility, local-update,
recoverability) to a pregeometric graph energy so realistic structure is more likely to
condense. As **physics**, that's a separate research program — out of scope here. As
**memory-graph operators**, several of those terms are exactly the right rules for keeping a
company-brain graph informative, navigable, and trustworthy.

**The one hard rule:** these are **local rules applied at the moment edges are created or
reinforced** (O(1)–O(degree) each), not a global objective optimized by simulated annealing.
A memory system *is* a set of rules about how relations form and persist; we adopt the RCO
*intuitions*, not the foam machinery.

| RCO term | Adopt as | Verdict |
|---|---|---|
| `H_mono` (CKW monogamy, squared strength budget) | conserved per-bead **strength budget** | **Primary — best fit** |
| `H_capacity` (linear total-strength cap) | weighted **anti-hub** fan-out cap | Adopt as the simple variant of the same lever |
| `H_law` (`−Σ q·K`) | **prior-seeded, earned** semantic edges | Adopt — already on the roadmap |
| `H_local-update` | **local-support gate** on new edges | Adopt — anti-hallucination |
| `H_recoverability` | graceful-degradation **principle** | Deferred — needs a metric |

Convergence note: these were derived independently from physics yet land on mechanisms the
product already wanted (earned semantics, anti-hub, anti-hallucination edges) — a signal
they're sound graph-memory principles, not borrowed metaphor.

---

## 1. Goals

- A bead's **relational strength is a conserved budget** — it can't be strongly about
  everything; reinforcing one association relatively decays its competitors.
- New associations are **seeded by a content prior `K`** and then **earned** (or overridden)
  by causal evidence over time.
- New associations require **local support** — no edges minted between unrelated beads.
- All as cheap local rules; no global optimization; agent judgment still gates real writes.

## 2. Non-Goals

- No Hamiltonian / annealing / Metropolis. No claim about spacetime.
- No auto-creation of authoritative associations — RCO rules are **gates and priors** on the
  existing agent-judged crawl, not new writers.
- One-way rule intact: these shape edge strength/candidacy, never bead content / claims / C-B-A.
- Not implementing recoverability as a term yet (§6).

---

## 3. Rule 1 — Monogamy: conserved per-bead strength budget (primary)

RCO: `H_mono = Σ_i [max(0, Σ_j q_ij² − Q_i²)]²` — penalize a bead whose **sum of squared edge
strengths** exceeds budget `Q_i`. Memory meaning: a bead has a **fixed pool of strong
relational commitment**; it can be strongly about a few things or weakly about many, but not
strongly about everything (a bead linked strongly to 50 things is a useless retrieval anchor —
it routes everywhere).

**Implementation (local, at reinforcement time):**
- Treat myelination edge strength as the `q_ij`. When an edge to bead `i` is reinforced, if
  `Σ_j q_ij² > Q_i²`, apply a **relative renormalization**: scale `i`'s incident strengths so
  the budget holds — i.e. reinforcing one path *relatively decays its competitors* (zero-sum
  attention per bead). This is a small change to `compute_myelination_bonus_map` / the reward
  fusion: cap and renormalize per-endpoint, not just globally clamp per-edge.
- `Q_i` default uniform, tunable; optionally higher for high-Assembly-Depth core beads.

**Why it matters for a company brain:** keeps anchors discriminative, makes myelination a
true *competition* (the strong path wins attention from weak ones), and is the sharpest of the
RCO terms — it bounds *strength concentration*, not just edge count.

---

## 4. Rule 2 — Capacity: weighted anti-hub fan-out cap

RCO: `H_capacity = Σ_i [max(0, Σ_j q_ij − B_i)]²` — bound a bead's **total** relational load.
This is the linear sibling of Rule 1 and the weighted form of the anti-hub guardrail from the
graph-navigability work.

**Implementation:** when a bead's total incident strength exceeds `B_i`, the maintenance pass
**prunes/decays its weakest incident edges** toward budget — suppressing runaway hubs that
degrade navigability. O(degree) per affected bead, on the maintenance cadence.

**Relationship to Rule 1:** monogamy (squared) caps *concentration*; capacity (linear) caps
*total load*. Ship **monogamy as primary**; capacity is the optional simpler cap. Don't run
both aggressively at once — pick the lever per tuning.

---

## 5. Rule 3 — Law prior: prior-seeded, earned semantic edges

RCO: `H_law = −Σ_ij q_ij · K(x_i, x_j)` — reward edges aligned with a prior compatibility
kernel `K` over bead features `x`. Memory meaning: **`K` is the hypothesis** ("these two beads
*should* plausibly relate," from embedding similarity + shared entities + type compatibility);
**`q_ij` is the earned strength**; causal/retrieval evidence lets `q` deviate from `K` over
time. This *is* "semantics begin as hypothesis, earned through experience."

**Implementation:**
- `K(x_i, x_j)` = blend of semantic-index cosine + shared-entity overlap + type-compatibility,
  computed from existing surfaces (no new model).
- Use `K` to **seed candidate associations** for the agent-judged crawler (a prior on what to
  consider), and as a **cold-start strength prior** before causal evidence accumulates.
- The effective edge strength blends prior and earned: `strength = (1−w_evidence)·K +
  w_evidence·q`, with `w_evidence` rising as myelination/retrieval evidence accrues — so the
  graph starts on language-model priors and shifts toward tenant-specific, outcome-grounded
  structure. (Same mechanism the storylines earned-semantics phase needs; share it.)
- **Guardrail:** `K`-seeded edges are *candidates/priors only* — they become real associations
  through the normal agent-judged crawl, never auto-written; and they stay in the
  interpretive/exploratory layer (retrieval expansion), not backbone derivation (one-way rule).

---

## 6. Rule 4 — Local-support gate (anti-hallucination edges)

RCO `H_local-update`: penalize edge changes unsupported by local structure. Memory meaning:
**a new association must be locally supported** — shared session/context, a short existing
path between the beads, entity overlap, or co-occurrence in a turn window — not minted between
beads with no relationship.

**Implementation:** a cheap precondition in the association write/crawl path: reject (or
down-rank to a low-confidence candidate) a proposed edge whose endpoints have no local support
(no shared context, no path within `L_min` hops, no entity overlap). Tightens the existing
lookback-window crawler with an explicit support check. Fights association hallucination —
critical for a *trustworthy* company brain.

---

## 7. Deferred — recoverability (principle, not yet a rule)

RCO `H_recoverability`: the state should be reconstructable / degrade gracefully. Memory
meaning: deleting one bead shouldn't lose a whole topic. Real value, but needs a concrete
metric (redundant path coverage / k-connectivity of a topic cluster / "re-derivable from the
graph," which SOUL already asserts for its projection). Track as a goal; operationalize later.

---

## 8. Guardrails

1. **Local rules, not global optimization.** Each rule fires at edge create/reinforce or on
   the maintenance cadence; O(1)–O(degree). No annealing, no Metropolis.
2. **Gates and priors, not writers.** RCO rules seed candidates, cap/renormalize strengths,
   and reject unsupported edges. Authoritative associations still require the agent-judged
   crawl. Myelination still only rewards concrete edges.
3. **One-way rule.** Strength/candidacy shaping only; never mutate beads/claims/C-B-A; `K`-edges
   serve retrieval expansion, not backbone derivation.
4. **Re-rank/decay, never delete content.** Capacity/monogamy weaken weak *edges*; bead
   content and directly-queried visibility are untouched.
5. **Hyperparameters tuned, not inherited.** `Q_i, B_i, K-blend, L_min, w_evidence` come from
   eval, not from the physics.

---

## 9. Rollout

1. **Rule 3 (`K` prior + earned blend)** — highest product value, shared with storylines
   earned-semantics; seeds better candidates and fixes cold start.
2. **Rule 1 (monogamy strength budget)** — make myelination a conserved per-bead competition.
3. **Rule 4 (local-support gate)** — anti-hallucination edge precondition.
4. **Rule 2 (capacity cap)** — optional weighted anti-hub, if hubs still appear after Rule 1.
5. **Deferred:** recoverability metric.

---

## 10. Success criteria

- High-degree beads stop dominating retrieval (anchor discriminativeness up; degree/strength
  tail down) with **no** recall loss on a labeled set.
- Cold-start retrieval is useful from day one (via `K`) and measurably shifts toward
  causal/earned structure as evidence accumulates (`w_evidence` rises; `q` diverges from `K`).
- Spurious/unsupported associations drop (local-support gate) without losing genuine
  cross-context links (which have entity/path support).
- All rules run on the existing write/maintenance path with no hot-path regression; one-way
  rule test green; no bead/claim/C-B-A mutation.

---

## 11. Open questions

- **A.** `Q_i`/`B_i` budgets — uniform vs. scaled by Assembly Depth / bead type.
- **B.** `K` composition (cosine vs. entity-overlap vs. type) and the `w_evidence` schedule
  (how fast earned `q` overtakes the prior).
- **C.** Local-support definition (`L_min`, which support signals count) and whether unsupported
  edges are rejected vs. quarantined as low-confidence candidates.
- **D.** Interaction with myelination decay (monogamy renormalization vs. existing per-edge
  reinforcement/decay — ensure one consistent strength update, not two fighting ones).

---

## 12. Honest caveat

RCO-as-physics (do law-priors make our spacetime more likely to condense?) is a separate
research program and is **not** what this PRD builds. What's portable is the constraint-rule
intuition: a memory graph is healthier when relational strength is a conserved budget,
semantics start as priors and are earned, and edges require local support. Those are local,
cheap, and serve the company-brain goal directly — adopt the rules, leave the foam.
