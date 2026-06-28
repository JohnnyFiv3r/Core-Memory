# PRD: Recoverability — No Single Point of Knowledge Failure

**Status:** Draft v1
**Owner area:** new `runtime/dreamer/recoverability.py`, the dreamer-run/health background job,
promotion/compaction selection, the Dreamer decide flow
**Builds on:** `graph-geometry-dynamics.md` (graph-health job + manifest pattern),
`assembly_depth.py` (high-value gating), `relational-constraint-rules.md` (`H_recoverability`,
deferred there → operationalized here)

---

## 0. Problem & definition

A company brain must not lose a topic because one bead is deleted, retracted, or corrupted
(people leave, docs get removed, a wrong bead is retracted). **Recoverability = no single
point of knowledge failure (SPOF):** for any single bead removed, the knowledge it carried
is still reachable another way, and derived artifacts (SOUL entries, storyline overlays,
claims) can still be re-derived from surviving evidence.

This is structural reachability on the association graph — fully computable, cheap, and
actionable. It is **not** a semantic correctness proof (see §9).

---

## 1. Goals

- Detect **SPOF beads** (articulation points) and **fragile links** (bridges) on the active
  association graph, `O(V+E)`.
- Score **per-topic resilience** (`κ_floor ∈ {1, ≥2}` — is the topic single-sourced?).
- Score **re-derivability** of derived artifacts from their evidence subgraph.
- Act on it: **protect SPOFs from eviction/decay** and **propose redundancy edges** to heal
  fragile high-value topics (agent-judged, never auto-written).
- Surface a tenant-level recoverability readout.

## 2. Non-Goals

- No exact vertex-connectivity (max-flow) — the biconnectivity test gives the `κ=1` vs `κ≥2`
  signal we need at `O(V+E)`.
- No auto-creation of associations — healing emits **candidates** through the existing decide
  flow. One-way rule intact.
- Not building redundancy everywhere (fights anti-hub/monogamy budgets) — heal **high-value
  topics only** (§5).

---

## 3. Module: `core_memory/runtime/dreamer/recoverability.py`

### 3.1 Graph construction
Build the **undirected** projection of the active association graph (recoverability is about
reachability, direction-agnostic):
```python
def _build_undirected(root) -> tuple[dict[str, set[str]], list[str]]:
    # read .beads/index.json "associations"
    # skip status in {"retracted","superseded","inactive"} (canonical filter)
    # adjacency[u].add(v); adjacency[v].add(u)
    # cap nodes at limit (default 5000, env CORE_MEMORY_RECOVERABILITY_LIMIT); report truncated
```

### 3.2 Articulation points, bridges, biconnectivity — one iterative Tarjan pass
Single DFS over the undirected graph yields all three. **Must be iterative** (explicit stack)
to survive 5000-node graphs without hitting Python recursion limits.
```python
def compute_block_cut(adjacency) -> BlockCut:
    # Iterative Tarjan with disc[] / low[] arrays.
    # Returns:
    #   articulation_points: set[str]            # removal increases component count
    #   bridges: set[tuple[str,str]]             # edge removal disconnects (low[v] > disc[u])
    #   biconnected_components: list[set[str]]    # "blocks"
    # Complexity: O(V + E), one pass.
```
Algorithm (standard Hopcroft–Tarjan, stated so it isn't hand-wavey):
- DFS tree; `disc[u]` = discovery index, `low[u] = min(disc[u], disc[back-edge targets], low[children])`.
- Root is an articulation point iff it has ≥2 DFS children.
- Non-root `u` is an articulation point iff some child `v` has `low[v] ≥ disc[u]`.
- Edge `(u,v)` is a bridge iff `low[v] > disc[u]`.
- Push `(u,v)` onto an edge stack; pop a block whenever an articulation condition fires.

### 3.3 Per-topic resilience
A "topic" is a derived cluster. Reuse existing surfaces in priority order:
1. **worldlines** (`graph/worldlines.derive_worldlines` — claim chains / entity threads /
   goal arcs): the product's real "topics";
2. fallback: connected components of the active graph.
```python
def cluster_resilience(cluster_bead_ids, block_cut) -> dict:
    # Induced-subgraph biconnectivity via the block map:
    #   single_sourced = cluster has an internal articulation point
    #                    OR |cluster| < 3 (a chain/pair is trivially κ=1)
    #   kappa_floor = 1 if single_sourced else 2
    # returns {cluster_id, kind, size, kappa_floor, single_sourced,
    #          articulation_members: [bead_id...]}
```
(`κ≥2 ⟺ induced subgraph is 2-vertex-connected ⟺ no internal articulation point and size ≥3`.
We never need exact `κ`; the gap between 1 and ≥2 is the entire product signal.)

### 3.4 Re-derivability of derived artifacts
For each derived artifact carrying evidence refs — SOUL revisions (`evidence`), storyline
overlays (`supporting_bead_ids`/`supporting_worldline_ids`), claims (`evidence_refs`):
```python
def evidence_survives_removal(evidence_bead_ids, adjacency) -> bool:
    # survivable iff: >=2 distinct evidence beads present AND
    #   for every single evidence bead b, the rest stay pairwise-reachable without b
    #   (cheap: evidence sets are small; run reachability on the induced evidence+1-hop graph)
def rederivability_fraction(root) -> float:   # survivable artifacts / total derived artifacts
```

### 3.5 High-value gate (so we don't over-heal)
```python
def _is_high_value(cluster_bead_ids, beads, depth_by_bead) -> bool:
    # True if cluster contains ANY of:
    #   - a human-confirmed/approved bead (authority=="user_confirmed" or approval_status=="approved")
    #   - a bead with assembly_depth >= CORE_MEMORY_RECOVERABILITY_MIN_DEPTH (default 0.5)
    #   - a bead linked to an active goal (goal_filters.is_active_goal on its goal neighbors)
```
Assembly depth pulled from `compute_assembly_depth(root, target_kind="*")` (already built in
the same job).

---

## 4. Build + persist + serve (mirror the geometry pattern exactly)

- **Build** in the `dreamer-run` job (`side_effect_queue.py`), immediately after
  `build_geometry_manifest` (shares the read of `index.json`):
  ```python
  from core_memory.runtime.dreamer.recoverability import build_recoverability_manifest
  rec_out = build_recoverability_manifest(root)   # best-effort, never fails the run
  ```
- **Persist** to `.beads/events/recoverability.json`, schema `recoverability_manifest.v1`:
  ```json
  {
    "schema": "recoverability_manifest.v1",
    "generated_at": "...", "total_bead_count": N, "truncated": false, "limit": 5000,
    "tenant_recoverability_score": 0.0,            // 1 - (SPOF beads / topic-relevant beads)
    "articulation_points": ["bead-..."],
    "bridges": [{"src":"bead-a","dst":"bead-b"}],
    "clusters": [
      {"cluster_id":"wl-...","kind":"entity_thread","size":7,"kappa_floor":1,
       "single_sourced":true,"high_value":true,"articulation_members":["bead-x"],
       "heal_candidate_id":"dc-..."}
    ],
    "rederivability": {"artifacts_checked": 0, "fraction_survivable": 0.0}
  }
  ```
- **Serve** read-only from disk (never recompute on read), mirroring `/v1/dreamer/geometry`:
  `GET /v1/dreamer/recoverability` (+ `/v1/memory/projection/recoverability` alias).
  `present=false` before the first build. `read_recoverability_manifest(root)` is the reader.

---

## 5. Maintenance rules (the part that makes it act, concrete)

### 5.1 Eviction / decay protection
```python
def is_recoverability_protected(root, bead_id) -> bool:
    # reads recoverability.json; True iff bead_id is an articulation point
    # of a high_value cluster (i.e., a SPOF for knowledge that matters).
```
**Wire-in:** the compaction/eviction candidate selector and the myelination decay path consult
`is_recoverability_protected()` and **exclude protected beads** from compaction/demotion.
This composes with the structural-stability work: coreness = "load-bearing", articulation =
"irreplaceable" — both feed `promotion_service` retention. (Protection re-ranks retention; it
never changes visibility — a directly-queried protected bead is returned regardless.)

### 5.2 Redundancy healing (constructive, agent-judged)
For each `high_value && single_sourced` cluster, emit a **`redundancy_candidate`** Dreamer
candidate (new `hypothesis_type`) proposing one association that would make `κ_floor ≥ 2`:
```json
{ "hypothesis_type":"redundancy_candidate", "cluster_id":"wl-...",
  "proposed_src":"bead-x","proposed_dst":"bead-y",
  "rationale":"topic is single-sourced through bead-x; linking x's dependents to y adds a 2nd path",
  "supporting_bead_ids":["bead-x","bead-y"] }
```
- `proposed_dst` chosen by the **`K` prior** (`relational-constraint-rules.md` Rule 3:
  embedding/entity/type compatibility) among existing beads that, if linked, break the
  articulation. Reuse the same `K` so we don't invent a second scorer.
- Flows through the **existing decide flow** — human/agent accepts before any edge is written.
  **Never auto-writes** (one-way rule; same governance as every other candidate).

---

## 6. Cost & cadence

| Step | Cost | Cadence |
|---|---|---|
| Undirected build + Tarjan (articulation/bridge/blocks) | `O(V+E)`, one DFS | every health run |
| Per-cluster resilience (block map lookup) | `O(Σ cluster sizes)` | every health run |
| Re-derivability (small evidence sets) | `O(artifacts · evidence·1hop)` | every health run |
| Redundancy candidate `K` scoring | `O(single-sourced high-value clusters · candidates)` | every health run |

All in the background job; reads served from the persisted manifest. Cap V at `limit` (5000)
consistent with geometry; report `truncated`.

---

## 7. Tests (`tests/test_recoverability.py`)

- **Articulation:** path `A–B–C` → `{B}`; triangle `A–B–C` → `{}`.
- **Bridge:** `A–B–C` → both edges bridges; triangle → none.
- **Cluster κ_floor:** chain cluster → `single_sourced=True`; cycle cluster (size≥3) → `False`.
- **High-value gate:** single-sourced cluster with no confirmed/high-depth/goal bead → not
  flagged for healing; same cluster with one human-confirmed bead → flagged.
- **`is_recoverability_protected`:** True for an articulation point in a high-value cluster;
  False for an articulation point in a low-value cluster and for a non-articulation bead.
- **Redundancy candidate:** emitted for high-value single-sourced cluster; is a *candidate*
  (decide flow), **not** an applied association; accepting it raises the cluster's `κ_floor`
  to 2 on the next build.
- **Re-derivability:** artifact with 1 evidence ref → not survivable; 2 connected → survivable.
- **Endpoint:** `present=false` before build, `present=true` after; served from disk.
- **One-way rule:** backbone derivation byte-identical with/without `recoverability.json`.

---

## 8. Guardrails

1. **Read-only metric + agent-judged healing.** Detection mutates nothing; healing is a
   candidate through the decide flow. One-way rule intact (`recoverability.json` never feeds
   backbone derivation).
2. **Re-rank retention, never suppress visibility.** Protection keeps SPOFs from being
   compacted/decayed; directly-queried beads always return.
3. **Heal high-value only.** Avoid redundancy bloat that fights anti-hub/monogamy budgets and
   re-introduces disordered-locality noise.
4. **Off the hot path.** Single DFS on the background cadence; retrieval reads the manifest.
5. **Structural, not semantic.** Recoverability proves the *graph/evidence survives* a
   deletion, not that meaning is preserved — a risk signal, not a correctness proof.

---

## 9. Rollout

1. **Detect + serve:** Tarjan articulation/bridge/cluster-κ + manifest + `GET /v1/dreamer/recoverability`.
2. **Protect:** `is_recoverability_protected` wired into compaction/decay selection.
3. **Re-derivability:** evidence-survival scoring for SOUL/overlay/claim artifacts.
4. **Heal:** `redundancy_candidate` via the decide flow (uses the `K` prior).

---

## 10. Success criteria

- Every active-graph SPOF and bridge is identified each run, served read-only.
- No high-value SPOF bead is ever compacted/evicted (protection holds on a labeled set), with
  no recall regression.
- Accepting a `redundancy_candidate` measurably raises a topic's `κ_floor` from 1 to ≥2.
- `tenant_recoverability_score` and per-topic `single_sourced` flags surface in the manifest;
  one-way-rule test green; no bead/claim/C-B-A mutation from detection.
