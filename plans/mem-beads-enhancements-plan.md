# Core Memory Enhancement Plan

## Overview

This plan captures enhancements to Core Memory (mem-beads) beyond the initial links spec. Items here refine the compaction model, token budgeting, and context injection behavior.

**Status**: Draft
**Prerequisite**: mem-beads-links-spec.md (v1 implementation)

---

## V1 Enhancements (Priority)

### 1. Session Object (REQUIRED FOUNDATION)
**Status**: V1 | **Complexity**: Low | **Priority**: FIRST

Define lightweight session record:
- **Required fields**: `id`, `started_at`, `ended_at`, `bead_ids`, `estimated_token_footprint`
- **Optional fields**: `session_digest_bead_id`

Without `bead_ids` explicitly attached, selection logic becomes expensive. Session records are the index for the rolling window.

---

### 2. Context Packet (OUTPUT INTERFACE)
**Status**: V1 | **Complexity**: Low | **Priority**: SECOND

Core Memory produces a "Context Packet" for the agent each turn.

**Schema:**
```json
{
  "packet_id": "packet-01KJN...",
  "built_at": "2026-03-01T12:00:00Z",
  "budget_tokens": 10000,
  "sessions_included": [
    {"session_id": "main-2026-03-01", "tier_mix": {"full": 5, "summary": 10, "minimal": 20}, "token_estimate": 3500}
  ],
  "beads": [
    {"id": "bead-001", "tier": "full", "render": "...", "score": 15.2, "session_id": "main-2026-03-01"}
  ],
  "edges": [
    {"source_id": "bead-001", "target_id": "bead-002", "type": "follows", "class": "authored"}
  ],
  "debug": {
    "dropped_beads": ["bead-003"],
    "reasons": {"bead-003": "budget_exceeded"},
    "truncations": []
  }
}
```

This makes testing deterministic and graph-export friendly.

---

### 3. Lifecycle State Machine
**Status**: V1 | **Complexity**: Medium

Formalize bead compaction as a state machine:
- States: `full` | `summary` | `minimal` | `tombstoned`
- Transition rules: session boundaries, age thresholds, token budget pressure

**Compaction Priority Rules** (downgrade order):
1. Non-promoted beads first
2. Non-pinned beads second
3. Non-root beads in chains third
4. Older sessions before newer

**Explicit Triggers:**
- `on_session_close(session_id)` → compaction pass for that session
- `on_context_build(query)` → NO compaction mutation (render-only)
- `on_budget_pressure()` → compaction pass (offline maintenance job)

**Hard Floor Invariant:**
- `min_sessions_keep` (default: 3-5)
- If still over budget after full minimization, drop oldest sessions entirely except pinned
- This prevents infinite downgrade loops

---

### 4. Canonical Render Formats
**Status**: V1 | **Complexity**: Low

Define per compaction tier with token budgets:

| Tier | Token Budget | Contents |
|------|--------------|----------|
| **Full** | ~200-400 tokens | title + summary bullets + key fields |
| **Summary** | ~50-120 tokens | title + 1-3 bullets + provenance line |
| **Minimal** | ~10-25 tokens | id + type + title only |

---

### 5. Token Budget Model
**Status**: V1 | **Complexity**: High

Specify two numbers:

- `context_budget_tokens`: Total budget (default ~10k)
- `max_session_tokens`: Cap per session (default: context_budget / N_sessions_target)

Without max_session_tokens, one large recent session can consume everything.

---

### 6. Rolling Window Algorithm
**Status**: V1 | **Complexity**: High

**Deterministic Pseudocode:**

```
sessions = sort_by_recency(all_sessions)
selected = []

for session in sessions:
    selected.append(session)
    while estimated_tokens(selected) > context_budget:
        oldest_non_pinned_bead = find_oldest_full_bead(selected)
        downgrade_compaction(oldest_non_pinned_bead)
```

Downgrade ordering is critical for determinism.

---

### 7. Context Assembly
**Status**: V1 | **Complexity**: Medium

**Two-Phase Assembly:**

**Phase 1 - Baseline Pack** (no query needed):
- Pinned beads
- Recent sessions (descending recency)

**Phase 2 - Query Expansion** (if query provided):
- Add beads/parents based on link proximity to query
- Uses link traversal to find causal neighbors

This prevents collapse when building packet without a query (e.g., "system tick").

---

### 8. Idempotent Build Requirement
**Status**: V1 | **Complexity**: Medium

**Core Rule:** Context packet assembly is pure/deterministic given (store state + query + budgets).

**What's Allowed to Mutate During Build:**
- NOTHING. Build must be read-only with respect to store.

**What's Only Updated on Write Operations:**
- Bead creation
- Edge reinforcement
- Compaction state changes
- Session records

This prevents "compaction-on-read" which makes debugging miserable.

---

### 9. Supersedes/Revises Injection Semantics
**Status**: V1 | **Complexity**: Medium

**Hard Rule**: If A supersedes B, B must NOT be injected unless:
- Explicitly requested, OR
- Needed to explain A (explain=true flag)

This prevents injecting contradictory decisions.

---

### 10. Links Drive Retrieval
**Status**: V1 | **Complexity**: Medium

Links shape injection:
- Prefer including a bead plus its causal parent(s) for coherence
- Prefer including the "current truth" in a supersession chain

**Default Traversal Sets:**
- **Why-chain types**: derives-from, extends, follows
- **What-changed-chain types**: supersedes, revises, reverse(derives-from)

---

### 11. Pinning
**Status**: V1 | **Complexity**: Low

First-class anti-eviction:
- `pinned: true` or `priority: high`
- Some beads must remain injectable even if old (e.g., core constraints)

---

### 12. Memory Salience Score
**Status**: V1 | **Complexity**: Low

**Baseline Scoring Formula:**
```
recency_weight = clamp(0..5) based on session age:
  - current session: 5
  - last 1-2 sessions: 4
  - last 3-5 sessions: 2
  - older: 0-1

inbound_component = min(3, log2(inbound_links + 1))  # Prevents hub dominance

score = (promoted ? 5 : 0) + (pinned ? 10 : 0) + recency_weight + inbound_component
```

---

### 13. Targeted Expansion
**Status**: V1 | **Complexity**: Medium

"If minimal bead appears in selected chain, allow targeted expansion fetch by ID."

**API Definition:**
```
expand_bead(bead_id) -> returns full render
```

**Rules:**
- Must bypass compaction tier
- Must NOT mutate compaction state automatically
- Expansion budget: separate `expansion_budget_tokens` OR expansion replaces lower-score beads to stay within budget
- Prevents expansion from breaking lifecycle machine

---

### 14. Tombstoned Semantics (Precise Definition)
**Status**: V1 | **Complexity**: Low

**Definition:**
- Tombstoned beads are NOT injected
- They REMAIN in store
- They REMAIN traversable for audit
- Eligible only when: superseded + not referenced + old + not pinned

**Invariant:** Tombstoning is NOT deletion. History is preserved.

---

### 15. Edge Authority Classes
**Status**: V1 | **Complexity**: Low

Add to edge schema:
```json
{
  "edge_class": "authored" | "derived"
}
```

- **Authored**: Created by agent (explicit link)
- **Derived**: Inferred by system (future myelination)

**Invariant:** Only derived edges are eligible for decay/pruning.

This is the seam for vNext myelination.

---

### 16. Reversible Compaction
**Status**: V1 | **Complexity**: Medium

**Hard requirement**: Compaction is render-layer only, NOT destructive mutation.

**Versioning Strategy:**
- Store full bead permanently in archive
- Store compaction state separately
- Render uses compact format; full content always recoverable

---

## V2 Enhancements

### Session Digest Bead
**Status**: V2 | **Complexity**: Medium

Auto-generated per-session summary bead:
- Top promoted beads
- Unresolved goals
- Key decisions
- Becomes default injection unit when budget is tight

---

## Implementation Order (Corrected)

1. Session object (#1) — REQUIRED FOUNDATION
2. Context packet interface (#2) — Lock down output first
3. Lifecycle state machine (#3)
4. Render formats (#4)
5. Token budget model (#5)
6. Rolling window algorithm (#6)
7. Context assembly (#7)
8. Idempotent build requirement (#8)
9. Supersedes semantics (#9)
10. Links drive retrieval (#10)
11. Pinning (#11)
12. Salience score (#12)
13. Targeted expansion (#13)
14. Tombstoned semantics (#14)
15. Edge authority classes (#15)
16. Reversible compaction (#16)
17. **V2**: Session digest bead (#17)

---

## Acceptance Criteria (Testing)

Add explicit tests per milestone:

- **Rolling Window Determinism Test**: Same store + same budget → identical packet output
- **Supersedes Rule Test**: If A supersedes B, and A is selected, B never appears unless explain=true
- **Budget Test**: Packet token estimate <= budget (with tolerance)
- **Idempotent Build Test**: Calling build twice with same inputs produces identical packets
- **Tombstoned Not Deleted Test**: Tombstoned beads remain in store and traversable

---

## Non-Goal (Explicit)

**Core Memory is NOT a Vector DB.**

Core Memory is deterministic, graph-based memory. It does not rely on embedding similarity for recall in V1.

This prevents drift into hybrid heuristics prematurely.

---

## Debugging Aids

### Context Packet Trace Log (Optional but Recommended)
- `packets/context_packet_<timestamp>.json` (ring buffer)
- Includes: drop reasons, token estimates, selected beads, excluded beads
- Makes "why didn't it remember X?" answerable

---

## References

- Core Memory spec: `plans/mem-beads-links-spec.md`
- Original beads: https://github.com/steveyegge/beads

---

# Patch Checklist (Sequenced for Minimal Rework)

This checklist sequences the enhancements to minimize rework and unblock Context Pack + compaction first.

## Phase 1: Foundation (Unblock Context Packet)

### 1. Choose Canonical Implementation
**Goal**: Avoid drift between `tools/mem-beads/mem_beads.py` and `tools/mem-beads/core_memory/*`.

**Patch**: Make `mem_beads.py` a thin CLI wrapper that calls core_memory APIs (or delete/freeze once migrated).

**Done when**: All CLI commands route through one library path.

---

### 2. Unify Storage Root + Derived Paths
**Goal**: Stop splitting beads vs edges across `.beads` / `.mem-beads` / hardcoded paths.

**Patch**: Introduce `MEMBEADS_ROOT` and derive:
- `sessions/*.jsonl`
- `index.json`
- `edges.jsonl`
- `events/*.jsonl` (optional)

**Done when**: Running CLI in any environment produces a single coherent store tree.

---

### 3. Fix Edge Direction to Match Spec
**Goal**: Align with "A derives-from B ⇒ source=A, target=B".

**Patch**: Change `make_bead()` link write from `add_edge(target, new)` to `add_edge(new, target)`; update traversal logic accordingly.

**Done when**: Why-chain traversal works without inverting semantics.

---

## Phase 2: Single Source of Truth

### 4. Make Edges the Single Source of Truth
**Goal**: Eliminate dual-truth: bead-body associations vs edge store.

**Patch**: Implement `migrate_links_to_edges()` and stop writing `source_bead`/`target_bead`/`relationship` inside beads.

**Done when**: All relationships appear in `edges.jsonl` and rebuild uses that.

---

### 5. Rebuild Index Must Replay All Event Types
**Goal**: Rebuild should deterministically reconstruct everything (supersedes, promotes, etc).

**Patch**: Expand event replay to handle supersede, promote, demote, etc., or normalize them into a single event schema.

**Done when**: `rebuild_index` produces the same results as a live run.

---

## Phase 3: Context Packet + Compaction

### 6. Implement Session Object Foundation
**Goal**: Unblock rolling window + token budgeting.

**Patch**: In `index.json` (or session registry), store per-session:
- `id`, `started_at`, `ended_at`, `bead_ids`, `estimated_token_footprint`

**Done when**: `bead_ids` can be retrieved without scanning session JSONL.

---

### 7. Add Context Packet Schema + Deterministic Builder
**Goal**: Lock output interface early; enable testing.

**Patch**: Implement `build_context_packet(query?, budget)` returning:
- Included sessions
- Bead renders
- Token estimate
- Drop reasons

**Done when**: Same store + inputs ⇒ identical packet output (idempotent).

---

### 8. Add Compaction State as Separate Metadata
**Goal**: Enforce reversible compaction.

**Patch**: Store compaction tier in separate index/metadata layer; never delete full bead content.

**Done when**: You can render any bead at full/summary/minimal without losing original fields.

---

## Phase 4: Graph + Myelination

### 9. Add edge_class: authored|derived Now
**Goal**: Safe seam for crawling/decay without touching causal truth.

**Patch**: Default all model-written edges to `authored`. Reserve `derived` for crawler.

**Done when**: Pruning code can target only derived edges by invariant.

---

## Phase 5: Robustness

### 10. Add Hard Failure-Mode Rules for Budget Overflow
**Goal**: Avoid infinite downgrade loops.

**Patch**: Add `min_sessions_keep` (e.g., 3-5). If still over budget after minimizing, drop oldest sessions except pinned.

**Done when**: Context packet always terminates and respects budgets.

---

### 11. Normalize Salience Scoring
**Goal**: Prevent "hub bead dominates forever".

**Patch**: Formalize recency_weight buckets and cap/log-scale inbound link component.

**Done when**: Scoring is stable across implementations and tests.

---

### 12. Add Context Packet Trace Log
**Goal**: Make "why didn't it remember X?" diagnosable.

**Patch**: Persist last K packets with drop reasons + token estimates.

**Done when**: You can inspect context packing decisions without reproducing manually.

---

## Future: Graph DB Abstraction

When extending to graph long-term memory (e.g., Neo4j), abstract edge operations behind a small interface:

```python
class EdgeStore:
    def add(self, source_id: str, target_id: str, link_type: str, **kwargs) -> dict
    def remove(self, edge_id: str) -> bool
    def neighbors(self, bead_id: str) -> dict  # {incoming: [], outgoing: []}
    def validate(self) -> dict  # integrity checks
    
# Swap implementations:
# - FileEdgeStore (current, JSONL-based)
# - Neo4jEdgeStore (future)
```

This allows swapping graph backends without rewriting business logic (chain traversal, cycle detection, etc.).

**Priority**: Low (not needed until V2 graph memory)
