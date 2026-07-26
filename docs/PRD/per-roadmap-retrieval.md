# PRD: Core Memory Perceptual Experience Retrieval and Junction Roadmap

Date: 2026-07-26

Status: Draft v1

Audience: Core Memory-native implementation agent

Source: ported from a host-application design review that grounds the PALMER
paper against product goals, storylines, and causal retrieval work. This Core
Memory copy is the engine-side contract.

Related host-application docs:

- **Causal root-cause retrieval PRD** — supersedes nothing; this PRD reconciles with its Algorithm 1 and Algorithm 2.
- **PALMER grounding and operationalization** — source grounding, gap analysis, and completeness review.
- **Confidence, backpressure, and myelination PRD** — `effective_confidence` and the two-tier reward.
- **Bead, storyline, goal, and root-cause PRD** — bead → worldline → storyline → goal derivation and the Dreamer convergence detector.

## Executive Summary

Core Memory should add two capabilities and reconcile them with the causal
root-cause retrieval PRD rather than building alongside it:

1. **PER (`segment_between`)** — given two anchors, return the best
   *actually-observed* bead chain connecting their junction neighbourhoods.
2. **A junction roadmap** — a maintenance-cadence projection whose vertices are
   recurring memory identities and whose edges are cached PER segments, over
   which goal-directed queries run as shortest-path plus concatenation.

The critical structural finding: **the root-cause PRD's Algorithm 1 and this
PRD's PER are the same search.** Algorithm 1 given a terminal set and a length
cap *is* `segment_between`. Run again one level up, over a graph whose vertices
are junction identities and whose edges are cached segments, it is the
roadmap query. Build one parameterized search, not two.

This is a direct port of PALMER (Beker, Mohammadi, Zamir, NeurIPS 2022,
arXiv:2212.04581) — retrieve observed trajectory segments from a replay buffer
and re-stitch them into goal-directed paths — with Core Memory's claim layer
supplying the reward function and the state identity that PALMER's method
presupposes but cannot provide outside robotics.

## Product Thesis

The causal root-cause PRD established that Core Memory should explain *how
remembered facts became true*. This PRD adds the mechanism that lets it explain
things **no single observed history contains**.

Today's expansion walks outward from seeds. Two histories that pass through the
same claim slot remain two results. The answer to "what connects the Q3 staffing
decision to the FreshKDS pilot outcome" may exist only as the *composition* of
two separately-observed histories that meet at a shared claim — and composition
is exactly what the current pipeline cannot produce.

The governing constraint, inherited from PALMER's differentiator over
distance-regression methods (SPTM, SoRB): **an edge may only exist if a real
chain was observed connecting its endpoints.** Learned distance estimates
overestimate on far-apart pairs and produce hallucinated shortcuts that corrupt
shortest-path queries. PER prevents this structurally by requiring the chain to
exist in the store before two identities may be called connected.

## Nomenclature

| Term | Meaning |
|---|---|
| Junction identity | A memory location that recurs: a `(subject, slot)` claim identity, a curated entity worldline, a goal bead. **Not** a raw bead |
| `N(a)` | Junction set of anchor `a` — beads sharing its junction identity |
| Segment `τ` | An actually-observed ordered bead chain, with its edges and claim refs |
| `R(τ)` | Segment reward: `−Σ edge_cost` over the chain, per root-cause PRD `:602-618` |
| Roadmap `G` | Directed graph: vertices are junction identities, edges are cached segments |
| Stitch | Concatenation of two segments at a shared junction identity |
| Seam | A stitch point where the crossing is *not* a stored association |

## Design

### 1. Junction identity is claims-first

This is the load-bearing design decision and the most likely thing to get wrong.

PALMER's junction test is an embedding-ball membership check, because in a robot's
replay buffer the same physical place is revisited constantly. **Core Memory's
corpus does not behave this way.** Raw bead embeddings almost never revisit the
same ball: every meeting transcript, every document section is a fresh point in
embedding space. If junction identity is defined on bead embeddings, junction
sets will be near-empty and the entire mechanism yields nothing.

What genuinely recurs is the interpretive layer: `(subject, slot)` claim
identities — "churn rate", "warehouse strategy", "FreshKDS pilot status" —
observed repeatedly across sources over months. **The claims layer is what turns
a stream of unrepeatable documents into a world with places.**

Junction identity resolution order:

| Priority | Signal | Junction cost |
|---:|---|---:|
| 1 | Same bead id | 0.0 |
| 2 | Same `(subject, slot)` claim identity | low |
| 3 | Shared curated entity worldline | medium |
| 4 | Embedding distance ≤ `d_p` | high |

Embedding proximity is the *fallback*, not the primary. Junction cost is charged
at query time when a path crosses that junction (see §5).

### 2. Threshold calibration from corpus statistics

Do not hard-code `d_p` or the entity-support thresholds. PALMER derives both of
its thresholds from the data's own one-step statistics, and the same recipe
applies:

- `d_p` := a conservative fraction of the mean embedding distance between
  **temporally adjacent beads within a single worldline backbone**. That
  distribution defines what "one step" means in this corpus.
- Entity-support and ubiquity bounds: derive from the observed support
  distribution rather than constants currently hard-coded by a host application
  (`MIN_WORLDLINE_SUPPORT = 2`, ubiquity `> 0.6` — `apps/web/src/lib/manifold/entity-quality.ts:147-158`).

Record the derived values in response metadata. A corpus that grows should move
its own thresholds.

### 3. PER: `segment_between`

```
segment(a, b) := argmax over observed chains τ of R(τ)
                 s.t.  τ_0 ∈ N(a),  τ_−1 ∈ N(b),  len(τ) ≤ cap
```

Return `null` when no real chain exists. **Never synthesize one.** A null result
is a correct result; a plausible fabrication is a defect.

Recommended signature, in the module the root-cause PRD already nominates
(`core_memory/graph/root_cause.py`, internal name `causal_attribution`):

```python
def segment_between(
    root: Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",          # upstream | downstream | any
    temporal_frame: str = "auto",
    relation_families: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
) -> dict | None:
    ...
```

Roadmap construction needs the plural form of this primitive; wrapping the
single best result is not sufficient:

```python
def segment_frontier_between(
    root: Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",
    relation_families: list[str] | None = None,
    max_expansions: int = 5_000,
    max_partitions: int = 32,
    max_results_per_partition: int = 2,
) -> dict:
    """
    {
      "segments": [...],             # bounded nondominated alternatives
      "complete": bool,              # queue exhausted inside the declared bounds
      "termination_reason": str,     # exhausted | expansion_cap | partition_cap
                                     # | result_cap | memory_pressure
      "expansions": int,
      "partitions_seen": int,
    }
    """
```

This is a multi-result mode on the same parameterized best-first search, not a
loop around `segment_between`. It continues after reaching a terminal, assigns
each observed terminal chain to its dynamic-cost partition (claim refs,
temporal coverage, source footprint, contradiction refs, and evidence refs), and
maintains a bounded Pareto set inside each partition. Paths are simple (no
repeated bead id) and `max_len` is finite, so exhausting the queue terminates and
is complete over the declared direction, relation families, and length cap.

`complete: true` is permitted only when the queue is exhausted. Hitting
`max_expansions`, `max_partitions`, or memory pressure while unexpanded states
remain returns `complete: false` with the corresponding termination reason. A
result cap that discards a nondominated terminal also returns `complete: false`,
even if the queue later exhausts. Roadmap construction must not cache that
partial frontier: it records the omission and leaves the pair to query-time
fallback. The public
singular `segment_between` may still stop at the first optimal terminal for a
fully specified temporal/source context; only
`segment_frontier_between` satisfies the roadmap-build contract.

#### Reconciliation with root-cause PRD Algorithm 1 — build once

Algorithm 1 (`docs/core-memory-causal-root-cause-retrieval-prd.md:884-925`) is
already this search minus a terminal condition. The deltas:

| | Algorithm 1 today | `segment_between` | `segment_frontier_between` |
|---|---|---|---|
| Termination | `max_depth` or no parents | First optimal terminal in `N(b)` | Queue exhaustion or explicit incomplete cap |
| Length | `max_depth` | `max_len` cap (segments are short) | Same `max_len` cap |
| Semantic drag | per hop | **off** — segments are query-independent and cacheable | **off** |
| Result | many ranked paths | Single best chain, or null | Nondominated partitioned frontier + completeness receipt |

**Requirement: implement one parameterized best-first search** with
`terminal_set`, `length_cap`, `drag_enabled`, and `result_mode=best|frontier`
parameters. Frontier mode must continue after a terminal and return the
completeness receipt above. With
`terminal_set=None, drag_enabled=True` it must reproduce Algorithm 1's current
behaviour exactly; that equivalence is a test, not a hope. Root-cause PRD Phase 2
and this section are one work item. Building them separately is the single
concrete way these workstreams waste each other.

#### Cost algebra: additivity is required

The root-cause PRD offers two confidence-penalty forms (`:727-734`) and leaves
the choice open. **Concatenative stitching forces the additive form.** Outer
shortest-path over concatenated segments is valid dynamic programming only if

```
R(τ₁ ∘ τ₂) = R(τ₁) + R(τ₂)
```

so use `confidence_penalty = −log(clamp(confidence, 0.001, 1.0))`. All other
`edge_cost` penalties and bonuses remain additive. After combining them, apply
the inherited floor to each edge before summing the path; §5 makes this ordering
explicit.

Non-additive path diagnostics — minimum semantic relevance, cold-hop counts,
claim-state summaries, `historical_confidence` / `current_truth_confidence` as
products — remain **metadata only**. They must never enter the cost used for
path selection, or DP optimality is silently lost.

### 4. The junction roadmap as a projection artifact

The roadmap is a derived, rebuildable cache in the same operational family as the
myelination manifest (`.beads/events/myelination-manifest.json`) — built on the
maintenance cadence, read at query time, never recomputed inline.

Construction (PALMER R-PRM Algorithm 1 with our nouns):

```python
def edge_cost_row(edge):
    return {
        "edge_id": edge["edge_id"],
        "cached_components": {
            "structural": edge["cost"]["structural"],
            "confidence": edge["cost"]["confidence"],
            "myelination": edge["cost"]["myelination"],
            "evidence": edge["cost"]["evidence"],
            "validation": edge["cost"]["validation"],
        },
        "dynamic_refs": {
            "source_ids": edge["source_ids"],
            "claim_refs": edge["claim_refs"],
            "temporal_refs": edge["temporal_refs"],
            "contradiction_refs": edge["contradiction_refs"],
            "evidence_refs": edge["evidence_refs"],
        },
    }

def build_junction_roadmap(root, *, max_vertices, radius):
    V = sample_junction_identities(root, max_vertices)   # see sampling below
    E = {}
    for a in V:
        for b in near(V, a, radius):
            alternatives = []
            result = segment_frontier_between(root, a, b, max_len=CAP)
            if not result["complete"]:
                record_omitted_pair(a, b, result["termination_reason"])
                continue
            for seg in result["segments"]:
                candidate = {
                    "segment_id": seg["segment_id"],
                    "segment": seg["bead_ids"],
                    "edges": seg["edges"],
                    "edge_cost_rows": [
                        edge_cost_row(edge) for edge in seg["edges"]
                    ],
                }
                alternatives = pareto_insert(alternatives, candidate)
            if alternatives:
                E[(a, b)] = alternatives
    return {"schema_version": "core_memory.junction_roadmap.v1", "V": V, "E": E}
```

`E[(a, b)]` is therefore an **alternative set**, not a single transition. The
builder must enumerate observed chains and retain a bounded nondominated
frontier. Partition candidates by the complete dynamic-cost signature: claim
refs, temporal coverage, source footprint, contradiction refs, and evidence
refs. Candidates from different partitions must not dominate one another.
Within a partition, dominance compares the per-edge cached component rows and
evidence/validation quality. This preserves, for
example, both a superseded chain that is valid historically and an active chain
that is valid for current truth until `temporal_frame` is known. It also
preserves a path supported by a narrower source set when a cheaper alternative
may be inaccessible to the caller. If a per-pair safety cap truncates the
frontier, it is a soft cap applied only after retaining the best candidate from
every partition. If mandatory partition representatives exceed a hard resource
limit, omit that roadmap pair, record the omission in `roadmap_meta`, and use
query-time fallback rather than silently discarding a temporal or source-scope
alternative.

**Vertex sampling.** PALMER samples vertices by visitation count. Our analogue is
junction support × label quality — the curation scores a host application
already computes in `storyline-curation.ts` / `entity-quality.ts`. Claim slots
with recurring observations and curated entity worldlines are high-value
vertices; ubiquitous background entities are not. Goal beads are always
vertices.

**Directionality — divergence from classic PRM.** Junction *membership* is
symmetric (two beads either occupy the same identity or do not), but segments and
roadmap edges are **directed**. `Near`/`Nearest` must respect direction: an
upstream query may only traverse segments whose normalized causal direction runs
cause→effect toward the anchor, per the root-cause PRD's direction table
(`:547-561`). Splicing a downstream segment into an upstream path merely because
the two share a junction bead is a defect class; test it explicitly.

**Store a complete per-edge component ledger, not a segment scalar.** Claim state,
temporal interpretation, and source accessibility are time-, frame-, or
caller-dependent. Baking them into `base_cost` at build time makes the cache
wrong for historical or permission-scoped queries. Each `edge_cost_row` stores
the edge id, its cached structural/confidence/myelination/evidence/validation
components, and the source/claim/temporal/contradiction/evidence refs needed to
compute dynamic components. A segment subtotal may be emitted as diagnostic
metadata, but it is never canonical input to path ranking.

### 5. Query time: cached versus query-dependent cost

Query cost = two vertex insertions + shortest path. Not graph-wide traversal.

| Component | When | Why |
|---|---|---|
| Structural cost | **Cached** per edge | Query-independent |
| Confidence penalty | **Cached** per edge | Uses additive `−log(confidence)` |
| Myelination term | **Cached** per edge | Query-independent at roadmap build |
| Evidence and validation terms | **Cached** per edge | Query-independent at roadmap build; may include bonuses |
| Temporal and contradiction terms | **Query time, per edge** | Hydrated from refs for the selected frame |
| Permission/evidence-gap terms | **Query time, per edge, after source filtering** | Depends on caller-visible evidence |
| Semantic drag | **Query time**, per segment | Depends on the question; applied per segment, not per hop |
| Junction-mismatch cost | **Query time**, per stitch | Depends on which junction tier joined the segments (§1) |
| Claim-state term | **Query time, per edge** | Selected by temporal frame (root-cause PRD `:1316-1329`) |

The component ledger must account for every term in the inherited `edge_cost`
model exactly once **and preserve its per-edge floor**:

```python
def score_segment(segment, query_context):
    edge_total = 0.0
    for row in segment["edge_cost_rows"]:
        raw_edge_cost = (
            sum(row["cached_components"].values())
            + sum(dynamic_edge_terms(row, query_context).values())
        )
        edge_total += max(0.001, raw_edge_cost)
    return edge_total + semantic_drag(segment, query_context)
```

`apply_query_costs` hydrates dynamic terms into each edge row, applies
`max(0.001, raw_edge_cost)` to that edge, and only then sums the segment.
Junction-mismatch cost is added once per stitch outside the segment loop.
Clamping an aggregate segment subtotal is forbidden: bonuses on one edge must
not cancel the inherited floor on another edge.

#### Segment alternatives require transition-aware search

Junction mismatch is not a property of either segment in isolation. It depends
on the endpoint bead of the selected incoming segment, the start bead of the
selected outgoing segment, and the junction identity that permits their stitch.
Therefore it cannot be assigned in `apply_query_costs` before path selection.

Build a directed line graph (or an equivalent relaxation state) after source
filtering and segment scoring:

```python
def segment_transition_graph(scoped_roadmap):
    L = Graph()
    for segment in scoped_roadmap.segment_alternatives:
        L.add_state(
            segment.segment_id,
            start_junction=segment.start_junction,
            end_junction=segment.end_junction,
            start_bead_id=segment.bead_ids[0],
            end_bead_id=segment.bead_ids[-1],
            segment_cost=segment.query_cost,
        )
    for incoming, outgoing in stitchable_pairs(L):
        L.add_transition(
            incoming.segment_id,
            outgoing.segment_id,
            cost=outgoing.segment_cost
            + junction_mismatch(
                incoming.end_bead_id,
                outgoing.start_bead_id,
                incoming.end_junction,
            ),
        )
    return L
```

The initial relaxation charges the first segment's cost once. Every subsequent
relaxation charges the outgoing segment plus the mismatch for that exact
incoming/outgoing pair. Search state is at minimum
`(junction_id, incoming_segment_id)`; an ordinary shortest path over junction
vertices is forbidden because it loses the previously selected endpoint.
All transition weights remain non-negative after the per-edge floors and
non-negative junction cost, so Dijkstra/best-first optimality remains valid.

**The same cached roadmap therefore carries two cost functions** —
`historical_confidence`-weighted and `current_truth_confidence`-weighted —
selected per query. This is a capability PALMER structurally cannot have: its
stored transitions never go stale. It is the direct answer to the paper's own
open question (§6: "when the environment undergoes a change, which transitions
in the replay buffer remain valid?").

Query procedure (R-PRM Algorithm 2 with our nouns):

```python
def plan_over_roadmap(
    query,
    anchors,
    goal_ids,
    roadmap,
    temporal_frame,
    *,
    allowed_source_ids=None,
    denied_source_ids=None,
):
    goal_terminal_set = resolve_goal_advancing_terminals(
        roadmap,
        goal_ids,
        temporal_frame=temporal_frame,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
    )
    if goal_ids and not goal_terminal_set:
        return fallback_goal_conditioned_expansion(
            query,
            anchors,
            goal_ids,
            terminal_relationship="advances_goal",
            allowed_source_ids=allowed_source_ids,
            denied_source_ids=denied_source_ids,
        )
    G = insert_query_vertices(
        roadmap,
        anchors,
        goal_terminal_set,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
    )
    apply_source_scope(
        G,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
    )
    hydrate_dynamic_costs(G, temporal_frame)                # time, claims, contradictions
    score_segment_alternatives(G, embed(query), temporal_frame)
    L = segment_transition_graph(G)                         # mismatch during relaxation
    path = shortest_path(
        L,
        start_states=states_reachable_from(anchors),
        terminal_states=states_ending_at(goal_terminal_set),
    )
    if path is None:
        if goal_ids:
            return fallback_goal_conditioned_expansion(
                query,
                anchors,
                goal_ids,
                temporal_frame=temporal_frame,
                terminal_relationship="advances_goal",
                allowed_source_ids=allowed_source_ids,
                denied_source_ids=denied_source_ids,
            )
        return fallback_expansion(
            query,
            anchors,
            temporal_frame=temporal_frame,
            allowed_source_ids=allowed_source_ids,
            denied_source_ids=denied_source_ids,
        )
    return concatenate_segments(path)                       # τ_stitched
```

`goal_ids` are identifiers for intention objects, **not terminal vertices**.
`resolve_goal_advancing_terminals` follows only accepted/validated
`advances_goal` associations in the correct direction, selects the evidence bead
on the advancing side, applies temporal and source scope, and maps each surviving
evidence bead into its roadmap junction neighbourhood. Goal mentions,
`supports` edges, and the Goal Bead itself do not satisfy the terminal
predicate. `goal_satisfied` is emitted only when the selected path ends at one
of these evidence terminals. If no advancing evidence is represented in the
roadmap, fallback expansion uses the same `advances_goal` predicate and scope;
if none exists in the graph, return `goal_unsatisfied` rather than terminating
at the intention object.

Source scope is a **pre-ranking traversal constraint**, not a post-hoc evidence
filter. Query insertion and roadmap traversal remove any alternative that
contains a denied source or a source outside a non-null allowlist before shortest
path runs. An `(a, b)` edge remains traversable only while at least one scoped
alternative survives. Excluded alternatives cannot contribute confidence,
cost, junction support, watershed mass, or returned evidence. The plan reports
how many alternatives were excluded; it never exposes their bead, edge, claim,
or source identifiers. If scoping disconnects the roadmap, fallback expansion
must receive the same allow/deny scope.

Emit `stitched: true` on composites, name the junction identities crossed, and
mark which crossings are **seams** (junction-joined but not stored associations).
Seam marking is what makes §7 possible and what lets adjudication discount a path
that leans on unproven crossings.

### 6. Watershed attribution over the roadmap

Root-cause PRD Algorithm 2 (`:978-1009`) currently propagates influence over raw
beads. **Run it over the roadmap instead.** Influence mass flows across segment
edges and accumulates on junction identities.

This is strictly better for root-cause attribution: a junction where multiple
observed histories converge is what a root cause *is*, which is what the rootness
score's `convergence_bonus` (`:1029-1036`) was already reaching for. It also runs
over far fewer nodes.

Keep bead-level attribution available for backward compatibility.

### 7. Seam healing — and its three mandatory guardrails

PALMER's executed plan is *better than the plan*, because physical execution
closes the endpoint mismatches that nearest-neighbour stitching left behind. Our
analogue: when a stitched path is validated, propose the seam crossing as a real
association, so next query it is a stored edge rather than a seam.

**This is the only self-reinforcing corruption path in the design, and the
guardrails are requirements, not recommendations.** The hazard: a user validates
an *answer*, not each crossing inside it. A spurious crossing riding along in a
correct-overall answer can be minted as a stored causal edge, feed future
segments, produce answers that get validated, and mint more edges. Physical
execution cannot be talked into this; human validation can.

1. **Never auto-write.** Seam-healed crossings enter the dreamer candidate review
   gate like any other proposal. PRD-A restricts causal promotion to the
   validated tier plus review (`:281-288`); that restriction is load-bearing here
   and must not be relaxed for seam healing.
2. **Provenance-tag the origin.** `origin: "stitch_healed"`, carrying the source
   path id and the junction tier that joined it. An untagged seam-healed edge is
   indistinguishable from an ingested one within a release cycle, which forecloses
   audit, decay, and bulk retraction.
3. **Attribute validation at path granularity where the surface allows it.**
   Answer-level thumbs is the weakest admissible signal and must mint candidates
   at correspondingly lower prior. Surfacing "this answer used N stitched
   crossings" changes what the human is actually endorsing.

Alongside healing, an accepted path emits `validated_outcome` reward events on
its traversed edges (PRD-A `:256-288`) and may be promoted to a first-class
storyline backbone. Without these writes, `R` never improves and the roadmap is a
static planner over a frozen corpus.

## API Contract

### `POST /v1/memory/segment-between`

```json
{
  "anchor_a": "bead_or_claim_identity",
  "anchor_b": "bead_or_claim_identity",
  "max_len": 6,
  "direction": "upstream",
  "temporal_frame": "auto",
  "allowed_source_ids": ["source_a", "source_b"],
  "denied_source_ids": ["source_private"]
}
```

Returns a segment object or `{"segment": null, "reason": "no_observed_chain"}`.

### `POST /v1/memory/plan` (or an optional mode on `trace_request`)

```json
{
  "query": "What connects the Q3 staffing decision to the pilot outcome?",
  "anchor_ids": ["bead_a"],
  "goal_bead_ids": ["bead_goal_1"],
  "temporal_frame": "current_truth",
  "allowed_source_ids": ["source_a", "source_b"],
  "denied_source_ids": ["source_private"],
  "max_vertices": 200
}
```

Response additions, alongside the existing `root_cause_attribution` object:

```json
{
  "plan": {
    "schema_version": "core_memory.stitched_plan.v1",
    "stitched": true,
    "segments": [
      {"segment_id": "seg_1", "bead_ids": ["..."], "cost": 0.31,
       "semantic_relevance_score": 0.72}
    ],
    "junctions": [
      {"junction_id": "claim:warehouse_strategy",
       "tier": "claim_slot", "is_seam": true, "junction_cost": 0.08}
    ],
    "total_cost": 0.74,
    "historical_confidence": 0.81,
    "current_truth_confidence": 0.44,
    "seam_count": 1,
    "goal_conditioning": {
      "goal_bead_ids": ["bead_goal_1"],
      "terminal_evidence_ids": ["bead_outcome_7"],
      "satisfied": true
    },
    "fallback_used": false
  },
  "roadmap_meta": {
    "built_at": "2026-07-26T00:00:00Z",
    "vertex_count": 184,
    "edge_count": 902,
    "alternative_count": 1174,
    "scope_excluded_alternative_count": 12,
    "d_p": 0.41,
    "d_p_source": "calibrated_from_backbone_adjacency"
  }
}
```

### Graceful degradation is mandatory

A new or sparse workspace will have near-empty junction sets and an empty
roadmap. Every surface must fall back to today's expansion behaviour and say so
in `fallback_used` / `limitations`. **Empty roadmap must never mean empty
answer.**

## Non-Goals

- Replacing semantic/vector anchor search — it remains the entry point, and it
  remains correct for the junction test's fallback tier.
- Replacing `causal_traverse(...)` or existing `/recall`, `/trace`, `/execute`.
- **Learning a long-range distance metric.** PALMER argues specifically against
  this: regressed distances overestimate on far-apart pairs and hallucinate
  shortcuts. `effective_confidence` is per-edge and derived from traversals that
  actually happened, which is the safe form. Do not extrapolate it to unobserved
  pairs.
- Importing PALMER's auxiliary losses (`p_fwd`, `π_inv`, `p_t`). There are no
  actions in this environment; they have no analogue.
- Claiming optimality. PALMER's guarantees assume dense coverage, a static
  additive `R`, and execution as ground truth. We have a sparse corpus, a
  time-varying `R`, and human validation. Inherit the architecture, not the
  confidence.

## Phasing

| Phase | Content | Depends on |
|---|---|---|
| 1 | Junction identity resolver + threshold calibration + `\|N(a)\|` corroboration counts | — |
| 2 | Parameterized best-first search; `segment_between`, complete `segment_frontier_between`, and root-cause Algorithm 1 unified; `−log` cost form | Phase 1 |
| 3 | Roadmap build job on the maintenance cadence; per-pair nondominated alternatives; complete component ledger; `roadmap_meta` | Phase 2 |
| 4 | Query-time planning, source-scope filtering, dynamic cost hydration, segment-state transition search, `advances_goal` terminal resolution, stitching, seam marking | Phase 3 |
| 5 | Watershed attribution over the roadmap | Phase 3 |
| 6 | Seam healing with all three guardrails; `validated_outcome` writeback; path promotion | Phase 4 + host-application feedback surface |

**Gate before Phase 2.** Phase 1 produces a junction-density diagnostic:
distribution of `|N(a)|` across the corpus, counted claims-first. If most
identities have empty junction sets, the corpus cannot support stitching and
Phases 2-6 should be deferred. This is a cheap query that gates expensive work;
run it first and report it.

## Test Scenarios

Beyond the root-cause PRD's existing scenarios, which must continue to pass:

**Algorithm 1 equivalence.** The parameterized search with `terminal_set=None,
drag_enabled=True` reproduces current Algorithm 1 output exactly.

**Plural frontier completeness.** Two observed chains connect the same
junctions through different temporal/source partitions. Expect both in the
frontier and `complete: true` only after queue exhaustion. Repeat with an
expansion cap reached first; expect `complete: false`, an explicit termination
reason, and no cached roadmap pair.

**Simple stitch.** History A: staffing decision → backlog → claim `X`.
History B: claim `X` → pilot delay → outcome. Expect one stitched path across
both, junction `X` named, `stitched: true`.

**No fabrication.** Two beads with no observed chain between them. Expect
`segment: null`, not a plausible path.

**Direction respected.** Two segments share a junction bead but run in opposite
causal directions. Expect no upstream path splicing them.

**Additivity.** `R(τ₁ ∘ τ₂) == R(τ₁) + R(τ₂)` for any two segments sharing a
junction, under the `−log` form.

**Temporal frame divergence.** One cached roadmap, two queries — historical and
current-truth — where the same junction pair has a superseded historical chain
and an active current chain. Expect both alternatives in the cache and different
path selection from the same cache.

**Complete cost accounting.** A segment with non-zero structural, confidence,
temporal, contradiction, permission/evidence-gap, myelination, evidence, and
validation terms. Expect the final additive cost to include every term exactly
once in per-edge rows, with cached and query-time terms reconciling to
`total_cost`.

**Per-edge floor.** A two-edge segment has raw edge costs `-1` and `2` after
bonuses and dynamic terms. Expect segment edge cost `2.001`, never aggregate
clamping to `1` or `1.001`.

**Source-scoped alternatives.** The unrestricted cheapest path uses a denied
source while a higher-cost allowed alternative connects the same junctions.
Expect the denied alternative to be removed before ranking, the allowed path to
win, and no denied identifier or score to influence the result. If no scoped
alternative survives, expect same-scope fallback expansion.

**Transition-dependent stitch ranking.** Two incoming and two outgoing segment
alternatives meet at one junction. The individually cheapest pair has a high
embedding-only mismatch, while a slightly costlier pair shares an exact bead and
has zero mismatch. Expect line-graph relaxation to choose the globally cheaper
combination after the pair-specific stitch cost; vertex-level preweighting must
not reproduce the wrong path.

**Goal-advancing terminal.** A Goal Bead is reachable through a mere mention,
while a different reachable evidence bead has an accepted `advances_goal`
association to that goal. Expect the evidence bead to be the terminal and
`goal_satisfied`; never terminate at the Goal Bead or mention. Repeat without
scoped advancing evidence and expect same-scope fallback or `goal_unsatisfied`.

**Sparse corpus.** Empty roadmap. Expect fallback to expansion, `fallback_used:
true`, a stated limitation, and a non-empty answer.

**Seam guardrails.** A validated stitched answer produces a candidate, never a
direct write; the candidate carries `origin: "stitch_healed"` and its source path
id; an answer-level-only validation mints at reduced prior.

**Cycle.** Roadmap containing a cycle terminates.

## Success Criteria

- Core Memory can answer questions whose evidence spans two histories that no
  single observed trajectory contains, and can name the junction where they meet.
- Retrieval never invents a connection: every edge in a returned plan corresponds
  to an observed chain, and null is returned when none exists.
- The same cached roadmap serves historical and current-truth queries with
  different path selection, including when the alternatives connect the same
  junction pair.
- A roadmap pair is cached only from a complete multi-result frontier; bounded
  early termination is explicit and falls back instead of masquerading as
  complete.
- Every inherited `edge_cost` term is represented exactly once in the cached plus
  query-time per-edge ledger, and the inherited floor is applied before segment
  summation.
- Allowed and denied source scope is enforced before insertion, ranking,
  attribution, and hydration; inaccessible alternatives cannot affect a plan.
- Junction mismatch is charged during segment-to-segment relaxation using the
  selected endpoint pair; alternative combinations cannot evade or pre-bake the
  stitch cost.
- Goal-conditioned planning terminates only on scoped evidence connected to the
  requested Goal Bead by accepted `advances_goal`, never on the intention object
  or a mere mention.
- Root-cause attribution accumulates on junction identities where histories
  converge, rather than on arbitrary upstream beads.
- Stitch rate and PER hit rate rise over time as validated paths become stored
  structure — evidence the loop is closed rather than open.

## Open Decisions

1. **Roadmap rebuild cadence** — nightly cron versus dreamer session-flush
   side-effect. Prefer reusing the existing myelination-update cadence.
2. **Vertex budget** — `max_vertices` scaling with workspace size; PALMER's
   roadmaps are dense, ours will be sparse and should stay small initially.
3. **Junction cost weights** per tier (§1) — conservative constants first, tuned
   once seam-crossing validation data exists.
4. **Whether `plan` is a distinct endpoint or a mode on `trace_request`** — the
   root-cause PRD favoured modes on existing surfaces; this PRD is agnostic.
