# PRD: Recall Effort Tiers & Relevance-Aware Causal Traversal

**Status:** Draft v1
**Owner area:** retrieval (`recall()` / `graph` traversal / myelination)
**Related:** `myelination-reinforcement.md` (edge strength), `dreamer-continuity-engine.md` (assembly depth / geometry), `docs/reports/emergent-geometry-substrate.md` (metric/well/curvature framing)

---

## 0. Problem

Every agent turn runs the retrieval pipeline (architectural invariant: retrieval
happens every turn, tiers walked cheapest-first). The current causal traversal is
a fixed-depth, query-blind best-first walk over the bead graph: it seeds from the
top semantic anchors, then expands by **structural** edge cost
(`graph/edge_weights.py`: relation weight, per-hop decay, provenance trust,
direction penalty, recency, supersession, myelination reinforcement) up to a
fixed `max_depth`/`max_chains`, and only *then* ranks. Two consequences:

1. **Latency.** "Lookup"-shaped questions ("what is X", "did we decide Y") pay
   for causal expansion + hydration they don't need. The effort tiers exist
   (`low`/`medium`/`high`) but the lightest still does a hop + (for medium/high)
   hydration, and the caller has three options to reason about.
2. **Query-blind expansion.** Query relevance enters **only** at seed selection
   and final ranking — never inside the walk. A bead is judged on its merits only
   if it already won the structural "path lottery." Myelination can boost a
   well-worn path toward a *popular-but-irrelevant* bead, injecting noise; a
   *relevant-but-structurally-weak* bead can be starved.

This PRD redefines the recall effort contract to two agent-chosen tiers, makes
causal expansion **agent-paged** (pagination-style "expand if the answer isn't
here yet"), and makes **query relevance a first-class traversal incentive**
alongside myelination — turning the walk into a goal-directed `g + h` best-first
search with early termination.

---

## 1. Goals

- Cut latency for lookup-shaped queries to ~semantic-search cost.
- Keep causal expansion "essentially free" by reusing the precomputed myelination
  manifest as a traversal incentive — **without** letting popularity inject
  irrelevant beads.
- Let the agent control expansion depth incrementally (paginate), rather than
  guess a fixed depth up front.
- Preserve every retrieval guardrail: re-rank never suppress; myelinated ≠
  correct; no well-trapping.

## 2. Non-Goals

- **Sub-Dijkstra shortest-path algorithms.** We evaluated Duan–Mao–Mao–Shu–Yin
  2025, *"Breaking the Sorting Barrier for Directed Single-Source Shortest
  Paths"* (`O(m log^{2/3} n)`, arXiv:2504.17033). It solves **full** SSSP
  (distances to *all* vertices) and achieves its speedup precisely by **giving up
  the by-distance ordering** (the same paper notes Dijkstra is provably optimal
  when an ordering is required). Core Memory's traversal is the opposite shape:
  **bounded, multi-source, early-terminating, and ordered** (we want ranked top
  chains, and we touch a tiny frontier, never all `n`). At memory-graph scale the
  asymptotic win is negligible and its constant factors/implementation complexity
  would lose to a tuned binary-heap best-first. **Decision: not adopted.** Revisit
  only if a global all-distances workload at multi-million-node scale ever
  appears (none today: assembly depth is 1-hop-bounded; the geometry export is
  `O(V+E)`, not SSSP).
- Auto-routing / query-parse classification of effort. The **agent** explicitly
  chooses the tier when it calls `recall()`. We do not add a heuristic router.
- Changing the write path, C/B/A, claims, or myelination reward sources.

---

## 3. Effort tiers (two, agent-chosen)

Collapse the public effort contract to **two** values so the agent's choice is
easy and hard to get wrong:

| Tier | Behavior | Agent picks when |
|---|---|---|
| `instant` | Semantic top-k only. **0 hops, no causal traversal, no hydration.** Returns ranked beads immediately. | "I know the fact/bead exists; just fetch it." Lookups, definitions, single-fact recall. |
| `trace` | Relevance-aware best-first causal expansion (§5), **agent-paged** (§4), with hydration on demand. | "I need the causal/temporal chain — why/how, what led to what." |

- **Backward compatibility:** `validate_recall_effort` aliases the legacy values
  — `low → instant`, and `medium`/`high` → `trace`. The agent-paged depth of
  `trace` subsumes the old fixed `medium` (≈ page 1, 1 hop) and `high` (≈ keep
  paging, deeper). No caller breaks; `dynamic` remains reserved/unused.
- The `recall()` tool description must define these two tiers crisply so the
  agent classifies well (this replaces the rejected auto-router).

---

## 4. Agent-paged causal expansion ("expand if not here yet")

`trace` is a **resumable, paginated** traversal, not a single fixed-depth run.

- Each `recall(effort="trace", …)` call expands **one bounded page** — a capped
  node-expansion budget (one hop-round from the active frontier).
- The result carries an **expansion control**:
  `expansion: { exhausted: bool, cursor: <token>, frontier_summary: [...] }`.
- The agent inspects the page. If the answer is present → stop. If not →
  `recall(effort="trace", expand=<cursor>)` resumes the walk from where it left
  off (next page). The **agent is the high-level goal test** at page boundaries —
  no LLM call inside the hot traversal loop.
- **Continuation state (open question §9.A):** the cursor must carry the visited
  set + frontier priority queue + accumulated chains so the next call *resumes*
  rather than restarts. Recommended: a short-TTL persisted traversal session
  keyed by cursor id (bounded, self-evicting) rather than a stateless token (the
  frontier can grow large).

### 4.1 Autonomous (non-interactive) callers

Batch/Dreamer/host callers that cannot paginate run `trace` single-shot with a
bounded total budget. There, the **cheap composite scorer (§5.2)** decides
per-chain termination, and an LLM goal-test is invoked **only for frontier beads
in the ambiguous band** `[θ_low, θ_high]` (a "close-call" escalation), never for
the clear-cut majority. Interactive `trace` stays LLM-free; autonomous `trace`
gets a confidence-gated LLM in place of the absent agent.

---

## 5. Relevance-aware best-first traversal (`g + h`)

The core change. Today the frontier priority is purely structural (`g`). We add
query relevance (`h`) as a co-driver, and we judge **every node on arrival**, not
only the ones that win the path lottery.

### 5.1 Priority

`priority(node) = combine(g, h)` where
- **`g`** = structural path cost to the node — today's blend: relation weight,
  per-hop decay, provenance, direction, recency, supersession, **and the
  myelination edge-bonus discount** (worn paths are cheaper to follow).
- **`h`** = query relevance of the node (cheap: cached embedding dot-product vs
  the query; lexical fallback when no embedding). This is **not** an LLM call.

Myelination thus stays an *incentive* in `g` (cheap deepening along strong
paths) while `h` independently pulls toward the query. A popular-but-irrelevant
path **deprioritizes itself** because its frontier nodes score low on `h`; a
relevant-but-weak path is lifted. This resolves the earlier "budget vs.
inclusion" tension: relevance is a **co-driver of the walk**, not a gate applied
after path selection.

### 5.2 Judge-on-arrival (Dijkstra's converging-paths lesson)

In Dijkstra, all incoming edges to a node are relaxed and the node's cost is the
min over paths. We borrow that: when a bead enters the frontier **by any path**,
evaluate its relevance/goal-fitness then — its inclusion and ranking must not be
decided by which path happened to reach it first/cheapest. A bead reachable only
via a weak path still gets judged; it simply expands later (lower priority).

### 5.3 Golden-hit early termination (per chain)

A chain terminates (stops deepening) when it reaches a **confident golden hit**:
a node whose composite score crosses `θ_gold` **and** is current-truth **and** is
grounded (observed/extracted, or confidence-class A/B). A semantically-close but
speculative C-class bead does **not** count as golden. Termination is **per
chain**, not global — each of the ~6–8 anchor chains stops independently. This is
a *work* bound, not a visibility bound (consistent with §6).

### 5.4 Architecture note

The graph layer (`graph/core.causal_traverse`) is query-agnostic today. Inject
relevance via a `relevance_by_bead` score map (computed once in the pipeline for
frontier candidates) or a scorer callback — keep the graph layer generic; do not
import the query text into `graph/`.

---

## 6. Guardrails (carried from Myelination PRD §26 + geometry §4)

1. **Re-rank, never suppress.** `instant` and golden-hit early-exit bound *work*,
   not *visibility*. Two protections: (a) `RecallResult` carries an **assurance
   signal** (coverage / top-score / golden-hit-found) so the agent can decide to
   escalate `instant → trace` or keep paging — escalation is the agent's job
   since there is no auto-router; (b) a **directly-named bead (exact entity/id
   match) bypasses the `h` relevance floor** — relevance/popularity gating must
   never hide something explicitly asked for.
2. **No well-trapping.** Strong-edge expansion is where a worn path loops to the
   comfortable cluster. The `h` co-driver is the primary brake; additionally do
   **not** traverse a strong edge into a *contradicted* bead (let the conflict
   signal flatten the well), and keep frontier selection from being purely greedy
   on myelination.
3. **Myelinated ≠ correct.** Every strong-edge-pulled bead still passes
   current-truth filtering (skip superseded/contradicted/inactive); the goal test
   weights grounding/confidence; fast-path results carry a `fast_path` /
   `assurance: low` marker so the agent knows it may want to escalate.

---

## 7. `RecallResult` contract additions

- `expansion`: `{ exhausted, cursor, frontier_summary }` (trace only).
- `assurance`: `{ coverage, top_score, golden_hit: bool }` — drives agent
  escalation decisions.
- Per-bead: `fast_path: bool`, existing `myelination_bonus`, new
  `query_relevance` (the `h` term used).

---

## 8. Rollout (sequenced, low→high risk)

1. **`instant` tier + `low` alias + assurance signal.** Config + small contract
   change; immediately removes traversal+hydration cost for lookups. Lowest risk.
2. **Myelination strong-edge incentive in `g`** (already partially present as a
   penalty discount) **+ `h` co-driver + judge-on-arrival.** The "free causal"
   win without popularity noise.
3. **Agent-paged `trace` with resumable cursor** (§4) + **golden-hit early
   termination** (§5.3). The most design-sensitive; needs the cursor contract and
   `θ` tuning. Fold `medium`/`high` into `trace` here.

A separate long-tail effort addresses non-traversal latency (vector tier,
hydration I/O), which this PRD does not.

---

## 9. Open questions (resolve before building §8.3)

- **A. Cursor / continuation contract:** stateless token vs short-TTL persisted
  traversal session. *Leaning: TTL session* (frontier can be large).
- **B. `h` source:** cached embedding dot-product vs lexical fallback, and
  whether `g + h` combines **additively or multiplicatively**.
- **C. `θ_gold` / `[θ_low, θ_high]` calibration:** too low stops early and hurts
  recall on genuinely multi-hop questions (the one job `trace` exists for); too
  high never fires. Make env-tunable; start conservative.

---

## 10. Success criteria

- Lookup queries via `instant` return at semantic-search latency with no
  traversal/hydration.
- `trace` returns a relevant first page fast and lets the agent stop early on
  golden hits; deep multi-hop answers remain reachable by paging.
- Popular-but-irrelevant beads do **not** surface from strong-edge expansion
  (measured against a labeled relevance set).
- No directly-named bead is ever hidden by relevance/popularity gating.
- Net p50/p95 recall latency improves on a representative query mix without a
  drop in answer grounding quality.
