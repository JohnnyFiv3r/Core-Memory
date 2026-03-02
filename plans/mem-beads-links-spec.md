# Core Memory Bead Links Specification

## Overview

This spec defines the explicit link types for Core Memory (mem-beads) — a causal memory system for AI agents. Links capture relationships between beads, enabling causal chain traversal and context-aware memory retrieval.

## Background

The original Steve Yegge beads library includes a rich dependency system for issue tracking (blocks, related, caused-by, discovered-from, etc.). Core Memory adapts this for agent memory, simplifying for session-based context while preserving causal relationships.

---

## Design Principles

1. **Links are first-class edges**, not embedded arrays on beads — stored in a dedicated dependency graph for efficient traversal and evolution.
2. **Scope is metadata, not structural** — link meaning doesn't change storage; `scope: session|cross_session` is filterable.
3. **Cycles are governed by invariants** — some link types must remain acyclic, others may cycle.
4. **No dual-truth** — migration from embedded `links[]` to edge store happens once, then edges are the sole source.

---

## Link Types

### Causal Links

| Type | Symbol | Direction | Meaning | Cycles Allowed? |
|------|--------|-----------|---------|-----------------|
| `follows` | `→` | source → target | Adjacent step in causal chain | No |
| `derives-from` | `⤵` | source → target | Built on or extends earlier bead | No |
| `supersedes` | `⬆` | source → target | Source replaces target as current truth | No |
| `extends` | `↗` | source → target | Extends prior decision/lesson | No |

### Response Links

| Type | Symbol | Direction | Meaning | Cycles Allowed? |
|------|--------|-----------|---------|-----------------|
| `responds-to` | `↩` | source → target | Response to specific prior bead | Yes |
| `continues` | `⧉` | source → target | Continues same work stream | Yes |

### Validation Links

| Type | Symbol | Direction | Meaning | Cycles Allowed? |
|------|--------|-----------|---------|-----------------|
| `validates` | `✓` | source → target | Confirms or proves earlier hypothesis | Yes |
| `revises` | `⚡` | source → target | Corrects or contradicts earlier bead | No |

### Contextual Links

| Type | Symbol | Direction | Meaning | Cycles Allowed? |
|------|--------|-----------|---------|-----------------|
| `context` | `◆` | source → target | Background context for source bead | Yes |
| `related` | `⇄` | bidirectional | Loose association | Yes |
| `recalls` | `⟲` | source → target | References prior session memory | Yes |

---

## Directional Convention

**Core rule**: `source_id` asserts a relationship TO `target_id`.

| Link Type | Assertion |
|-----------|-----------|
| A `derives-from` B | A was built on B |
| A `follows` B | A comes after B in causal order |
| A `supersedes` B | A is the current truth, B is deprecated |
| A `validates` B | A confirms B's truth |
| A `recalls` B | A references B from prior session |
| A `responds-to` B | A is a response to B |

---

## Cycle Detection Rules

**Must remain acyclic** (enforced):
- `follows`
- `derives-from`
- `extends`
- `supersedes`
- `revises`

**May cycle** (no restriction):
- `responds-to`
- `continues`
- `related`
- `context`
- `recalls`
- `validates`

**Implementation**: Before adding an edge of a protected type, run cycle detection. Reject the edge if it would introduce a cycle.

---

## Storage Architecture

### Edge Store Schema

Store edges in a dedicated JSONL file (or future DB table):

```jsonl
{"source_id": "bead-001", "target_id": "bead-002", "type": "derives-from", "created_at": "2026-03-01T04:00:00Z", "scope": "session", "thread_id": "thread-bead-001"}
{"source_id": "bead-003", "target_id": "bead-001", "type": "validates", "created_at": "2026-03-01T04:05:00Z", "scope": "session"}
```

### Edge Schema

```json
{
  "id": "edge-001",
  "source_id": "bead-001",
  "target_id": "bead-002",
  "type": "derives-from",
  "scope": "session",        // "session" | "cross_session" | "context"
  "thread_id": "thread-001", // Optional: groups related beads
  "metadata": {
    "confidence": 0.95,
    "note": "Evidence from test results",
    "origin_turn": 42,
    "origin_session_id": "main-2026-02-26"
  },
  "created_at": "2026-03-01T04:00:00Z",
  "updated_at": "2026-03-01T04:00:00Z"
}
```

### Metadata Schema (Shared Keys)

| Key | Type | Description |
|-----|------|-------------|
| `confidence` | float | 0.0-1.0 certainty of the relationship |
| `note` | string | Human-readable note |
| `evidence_ids` | array | Bead IDs providing evidence |
| `origin_turn` | int | Turn number where link was created |
| `origin_session_id` | string | Session where link was created |

---

## Bi-Directional Storage

**Recommendation**: Store single directed edge + build reverse indexes on query.

Do NOT physically store two edges (A→B and B→A with mirrored types) — this creates drift.

**Implementation**:
- Edges stored once: `{source_id, target_id, type, ...}`
- Query-time: derive reverse neighbors via index lookup
- Index: `by_target[target_id] → [{source_id, type, ...}]`

---

## Uniqueness Policy

**Rule**: Unique on `(source_id, target_id, type)`.

- If identical edge re-asserted, update `metadata` and `updated_at`
- Multiple edges between same pair with different `type` are allowed (e.g., A relates-to B AND A derives-from B)
- Multiple edges with same type but different metadata: reject unless metadata change is intentional

---

## Thread/Stream Support

`thread_id` (or `stream_id`) groups related beads into conversations or work streams.

### Deriving Thread Roots

- Thread root = first bead in a stream (lowest `created_at` or lowest `turn`)
- Recommended format: `thread-<root_bead_id>`

### Ordering Within Thread

- Sort by `created_at` or `turn` number
- `continues` and `responds-to` should reference within same thread

---

## When to Use Which Link

### `responds-to` vs `continues` vs `follows`

| Situation | Use |
|-----------|-----|
| Answering user's specific question | `responds-to` |
| Continuing same work stream, not adjacent | `continues` |
| Adjacent step in causal chain (A led to B) | `follows` |
| Building on evidence/earlier bead | `derives-from` |
| Confirming earlier hypothesis | `validates` |
| Correcting earlier mistake | `revises` (to prior) |

---

## Minimum Linking Policy

To prevent graph-rot (orphan beads with no relationships):

| Bead Type | Requirement |
|-----------|-------------|
| `decision` | MUST have `derives-from` or `responds-to` |
| `outcome` | MUST have `derives-from` or `follows` |
| `lesson` | SHOULD have `derives-from` or `validates` |
| `goal` | SHOULD have `context` |
| `evidence` | MUST have `context` (source of evidence) |
| `reversal` | MUST have `revises` (to prior bead) |
| `failed_hypothesis` | SHOULD have `revises` or `derives-from` |
| `checkpoint` | SHOULD have `follows` |

---

## Compaction and Retrieval

### Compaction Tiers

| Tier | Sessions Back | Content | Links |
|------|---------------|---------|-------|
| **Full** | 0-1 | All fields | Full |
| **Summary** | 2-5 | title + summary only | Full |
| **Minimal** | 6+ | title + type + ID only | **Preserved** |

**Key principle**: Links are never deleted. Only content compacts.

### Dangling Links

If target bead is compacted/missing:
- Mark as `dangling: true` in query results
- Never auto-delete edges
- Allow "expand bead X" on demand to retrieve from archive

### Retrieval Algorithm

1. If target is Full/Summary: include all fields
2. If target is Minimal: include only `(id, type, title)` but still traverse
3. If target is missing/dangling: include `(id, dangling: true)` with available metadata

---

## Chain Queries

### Up-Chain (Find Roots)

Follow: `derives-from` → `extends` → `follows`

Returns: beads that led to the current bead

### Down-Chain (Find Effects)

Follow: `validates` → `supersedes` → `revises` → reverse(`derives-from`)

Returns: beads that resulted from this bead

### Default Query Sets

| Query | Types to Traverse |
|-------|-------------------|
| `up` | derives-from, extends, follows |
| `down` | validates, supersedes, revises, reverse(derives-from) |
| `context` | context, related, recalls |
| `responses` | responds-to, continues |

---

## Supersession Semantics

- `A supersedes B` means: **use A as current truth**
- Retrieval SHOULD prefer A unless explicitly asked for history
- Multiple active superseders of same bead: disallow unless `branching: true` in metadata

---

## Migration Note

If existing beads have embedded `links[]` arrays:

1. Write one-time migration: extract `links[]` to edge store
2. After migration: stop writing embedded links
3. Verify: edge store is source of truth, no dual-truth bugs

---

## Naming Conventions

| Entity | Format | Example |
|--------|--------|---------|
| Link types | kebab-case | `derives-from`, `responds-to` |
| Bead IDs | Existing format | `bead-01KJE0XFWH0E8TDDN...` |
| Thread IDs | `thread-<root_bead_id>` | `thread-bead-001` |
| Edge IDs | `edge-<nanoid>` | `edge-01KJF...` |

---

## Deferred Features (Not Used in v1)

These beads features are not used in Core Memory v1 but the dependency framework supports them for future use:

| Feature | Status | Future Use |
|---------|--------|------------|
| `blocks` | Deferred | Could block retrieval |
| `parent-child` | Deferred | Could group beads |
| `waits-for` | Deferred | External conditions |
| `conditional-blocks` | Deferred | Branching logic |
| `authored-by` | Deferred | Multi-agent context |
| `approved-by` | Deferred | Approval workflows |

The underlying edge store can accept any dependency type; these are simply not generated by Core Memory v1.

---

## CLI Plan

For internal tooling parity with Beads:

| Command | Description |
|---------|-------------|
| `link add <source> <target> <type>` | Create edge |
| `link rm <source> <target> [type]` | Remove edge |
| `link list <bead_id>` | List edges from/to bead |
| `link tree <bead_id>` | Visualize causal chain |
| `link neighbors <bead_id>` | Show immediate connections |
| `link cycle-check` | Detect cycles in protected types |
| `link query --up <bead_id>` | Traverse up-chain |
| `link query --down <bead_id>` | Traverse down-chain |

---

## Bead Schema (Updated with Link References)

```json
{
  "id": "bead-01KJE0XFWH0E8TDDNCC5EYVCT5",
  "type": "decision",
  "title": "Use Python stdlib for mem-beads CLI",
  "summary": ["Avoid external dependencies", "Simpler install experience"],
  "status": "active",
  "session_id": "main-2026-02-26",
  "turn": 42,
  "tags": ["mem-beads", "tech-stack"],
  "scope": "project",
  "created_at": "2026-02-26T22:25:38.193789+00:00",
  
  "created_via": "bead_marker",  // "bead_marker" | "manual" | "migration"
  " compacted": false,
  "compacted_at": null
}
```

**Note**: Links are no longer embedded. All relationships stored in edge store.

---

## References

- Original beads: https://github.com/steveyegge/beads
- Dependencies: `beads-main/docs/DEPENDENCIES.md`
- Types: `beads-main/internal/types/types.go` (DependencyType)
