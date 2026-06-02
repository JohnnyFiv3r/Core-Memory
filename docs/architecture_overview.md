# Architecture Overview

**Status:** Canonical

## What Core Memory is

Core Memory is the causal memory layer for AI agents. It provides a persistent,
queryable knowledge graph built from conversation turns:

- **Beads** — structured records of what happened, what was decided, what was learned
- **Causal edges** — agent-judged links threading beads together so future turns can
  reason about prior context
- **A write side** that captures and promotes beads as the conversation unfolds
- **A retrieval side** that finds relevant beads for any new query and reasons across
  the causal graph to assemble an answer

Core Memory owns the bead schema, causal edge semantics, supersession logic, rolling
window mechanics, claim resolution, and recall semantics. The datastore, the semantic
indexing layer, and the framework integration (MCP, PydanticAI, OpenClaw, etc.) are all
pluggable.

---

## The two-sided model

Both sides are anchored on `session_id`. The Core Memory Skill is the agent-side
companion that issues retrieval calls and shapes search forms.

```
┌──────────────── Retrieval-side ────────────────┐   ┌────────────────── Write-side ──────────────────┐
│                                                │   │                                                │
│  Context Injection                             │   │  Context Injection                             │
│                                                │   │                                                │
│  PER TURN:                                     │   │  AT SESSION START:                             │
│    agent parses query                          │   │    inject "rolling window" beads               │
│    agent fills search form                     │   │                                                │
│    agent uses search or reason (or both)       │   │  AT AGENT_END (per turn):                      │
│    agent utilizes causal memory in answer      │   │    write full bead                             │
│                                                │   │    append associations                         │
│  [Core Memory Skill is the agent companion]    │   │    promote or promote-candidate                │
│                                                │   │                                                │
│                                                │   │  AT SESSION END:                               │
│                                                │   │    archive full beads + associations           │
│                                                │   │    compress non-promoted beads,                │
│                                                │   │      update "rolling window"                   │
│                                                │   │                                                │
│  Memory flush                                  │   │  Memory flush                                  │
└────────────────────────────────────────────────┘   └────────────────────────────────────────────────┘
                  └────────────── session_id binds both sides ──────────────┘
```

---

## Session lifecycle (5 stages)

### 1. Session start
- **Trigger:** agent boot, new conversation, explicit `process_session_start`
- **Action:** Inject the current rolling window beads as background context for the
  agent. The rolling window is the compressed continuity of prior sessions — promoted
  beads carry full content, everything else is compact (type / title / associations,
  looked up by bead_id when needed).
- **Outcome:** The agent starts pre-warmed with historical context.

### 2. Per turn (retrieval-side)
- **Trigger:** agent receives a user query
- **Action:** The agent retrieves on **every turn** — even checking current session
  context is a retrieval step. The agent parses the query, fills a search form, and
  walks the retrieval tiers depth-first (cheapest first) until context is sufficient.
- **Retrieval tiers (cheapest first):**
  1. Rolling window / current session lookup — the starting tier, always consulted
  2. Semantic candidate retrieval (entity registry + semantic index)
  3. Causal reasoning — walk causal edges from candidate seeds
  4. Full transcript hydration — recover the cited turns for source-grounded answers
- **Outcome:** An answer grounded in causal memory. Tier depth varies per query; the
  rolling window check is always tier 1, not a bypass condition.

### 3. Agent end (write-side, per turn)
- **Trigger:** agent finishes a turn (canonical entry: `emit_turn_finalized` →
  `process_turn_finalized`)
- **Action:**
  1. Write the full bead for this turn
  2. Append associations to prior beads (always agent-judged)
  3. Promote the bead, or mark it as a promotion candidate, based on type and confidence
- **Outcome:** A new bead lives in the working session context with its causal links.

### 4. Session end / compaction
- **Trigger:** session close / flush event / compaction
- **Action:** Archive promoted beads with full content + associations to the long-term
  causal graph. Compress non-promoted beads to compact form.
- **Outcome:** The session has been folded into the persistent memory graph.

### 5. Memory flush
- **Trigger:** explicit flush, scheduled cycle
- **Action:** Update the rolling window so the next session starts pre-warmed.
- **Outcome:** Continuity is preserved across sessions in a token-efficient form.

---

## Module ownership

| Layer | Modules | Responsibility |
|---|---|---|
| Schema | `schema/`, `temporal/` | Bead/turn/association data shapes; temporal resolution |
| Persistence | `persistence/` | Store, projection cache, pluggable storage backends |
| Graph tier | `persistence/graph/` | Pluggable causal graph backends; `GraphBackend` protocol + factory |
| Domain logic | `claim/`, `entity/`, `association/`, `graph/` | Claim extraction & resolution, entity registry, association inference, causal graph operations |
| Retrieval | `retrieval/` | Tiered recall pipeline (rolling window → semantic → causal → hydration) |
| Runtime | `runtime/{turn,flush,session,passes,queue,observability,dreamer}/` | Turn orchestration, write flow, side-effect queue, dreamer, observability |
| Public API | `core_memory/__init__.py`, `memory.py`, `transcript_ingest.py` | Curated surface for consumers |
| CLI | `cli/{parsers,handlers}/` | Command-line surface; entry point `core_memory.cli:main` |
| Integrations | `integrations/{openclaw,pydanticai,mcp,http,obsidian,…}/` | Framework adapters — consume the public API |

**Layering law:** dependencies flow downward only:
```
schema → persistence → domain logic → retrieval → runtime → integrations (adapters)
```
Nothing imports upward. Adapters consume the public API in `core_memory/__init__.py`
and do not extend or override core semantics.

---

## Subsystems

| Subsystem | Status | Purpose |
|---|---|---|
| Bead schema, turn ingestion, persistence | **Core / always on** | Foundation |
| Association crawler | **Core / always on** | Tracks causal links between turns; always agent-judged |
| Claim extraction | **Core / always on** | Detects repeated themes, synthesizes into rules, tracks "current truth" as conversations progress |
| Entity registry | **Core / always on** | IDs candidate beads by semantic keyword; consulted as tier 2 in every retrieval pass |
| Rolling window / compaction | **Core / always on** | Token-efficient continuity across sessions; compact beads carry type/title/associations only |
| Retrieval pipeline | **Core / always on** | Tiered recall — always executes, starting at tier 1 (rolling window) every turn |
| Semantic indexing | **Opt-in (today)** | Tier-2 candidate retrieval. Currently gated behind faiss/pgvector. Direction: more universally available via the capability-tier adapter (Phase 6). |
| Dreamer | **Background, opt-in** | "Move 37" — proposes novel associations across the bead graph as creativity. High-signal results can inform SOUL.md. |
| Myelination | **Future / in active development** | Causal edges harden by validity + retrieval frequency; decay when stale, superseded, or never retrieved. Replaces vibes-based pruning with schema-driven pruning. |
| SOUL.md | **Future / emerging** | Agent-authored identity that evolves over time. Informed by claims + dreamer + myelination. Concept from Peter Steinberger (https://soul.md). |

---

## Adapter philosophy

All frameworks are adapters. None are first-party.

Current adapters:
- **OpenClaw** — the original testbed; currently over-coupled to core in ways the
  cleanup workstream is deliberately unwinding (Phase 9). New code must not deepen this
  coupling.
- **MCP** — Model Context Protocol server for Claude Code, Cursor, etc.
- **PydanticAI** — in-process tool integration
- **SpringAI / HTTP** — service bridge for Java/Spring orchestrators
- **LangChain** — `CoreMemory`, `CoreMemoryRetriever`
- **CrewAI** — multi-agent crew memory
- **Neo4j** — pluggable graph backend with read + write paths (Phase 7 complete)
- **Graphiti** — temporal knowledge graph backend via `graphiti-core`; self-hosted or
  Zep-hosted alias
- **Obsidian** — write-only vault mirror via `BeadSyncTarget` protocol

Adapters consume the public API. They do not import from `core_memory/runtime/`,
`core_memory/persistence/`, `core_memory/retrieval/`, or any other internal module.

---

## Storage and capability model

Storage is pluggable behind the `StorageBackend` protocol (`persistence/backend.py`).
`JsonFileBackend` (default, no deps) and `SqliteBackend` ship. `create_backend()` is
the factory; reads `CORE_MEMORY_BACKEND`.

Backends declare capability flags:

```python
@dataclass
class BackendCapabilities:
    vector_search: bool          # backend can do search_candidates()
    graph_traversal: bool        # backend can do traverse() natively
    full_text_search: bool       # backend can do lexical_lookup() natively
    transcript_hydration: bool   # backend can do hydrate_turn_refs() natively
```

Python fallbacks remain for backends that declare `False`.

### Graph tier (Phase 7 — complete)

The graph tier is separately pluggable via `GraphBackend` protocol
(`persistence/graph/protocol.py`). `create_graph_backend()` reads
`CORE_MEMORY_GRAPH_BACKEND`.

| Provider | Capabilities |
|---|---|
| `kuzu` (default) | Embedded; graph traversal; zero deps |
| `neo4j` | Graph traversal; requires `core-memory[neo4j]` |
| `graphiti` | Temporal KG + vector search; requires `core-memory[graphiti]` |
| `zep` | Graphiti on Zep-hosted cloud; same extra |

Custom backends register via `register_graph_backend(name, factory)`.

Write-only vault mirrors use the separate `BeadSyncTarget` protocol
(`integrations/obsidian/protocol.py`) activated via `CORE_MEMORY_SYNC_TARGETS`.

See `docs/graph_backend_plugin.md` for the plugin API reference.

---

## Public API

Curated surface in `core_memory/__init__.py`:

| Symbol | Purpose |
|---|---|
| `Memory`, `MemorySession`, `capture` | Quick-start helpers |
| `recall(query, effort=...)` | Primary retrieval entry point |
| `process_turn_finalized`, `process_session_start`, `process_flush` | Runtime write boundaries |
| `memory_search`, `memory_trace`, `memory_execute` | Tool surface for agents |
| `emit_turn_finalized` | Adapter/helper write ingress |
| `Turn`, `EvidenceItem`, `SourceItem`, `RecallResult` | Schema types |
| `StorageBackend`, `JsonFileBackend`, `SqliteBackend`, `create_backend` | Storage adapter surface |
| `Bead`, `Association`, `Event`, etc. | Schema types |
| `MemoryStore`, `DEFAULT_ROOT`, `DiagnosticError` | Compatibility/advanced surface |

---

## Non-goals

- Transcript / index dump replay is **not** the primary write authority. Transcripts
  are bridge inputs, not canonical writes.
- Core Memory is **not** a vector search engine; it composes vector search as one
  tier in retrieval.
- Core Memory is **not** a graph database; it composes graph traversal as one tier in
  retrieval and treats Neo4j (etc.) as a pluggable backend.
- Core Memory does **not** define a framework. Agents own their prompting, tools, and
  control flow. Core Memory is called by agents; it does not call agents (except
  through the agent-judged crawler, which is configured per integration).

---

## References

- `docs/status.md` — single source of truth for open work and completion state
- `docs/cleanup-plan.md` — cleanup/refactor workstream (phases 0–10) with per-step checkboxes
- `docs/PRD/README.md` — index of all PRD specs
- `docs/index.md` — full docs navigation
- `docs/graph_backend_plugin.md` — graph backend + sync target plugin API
- `docs/canonical_surfaces.md` — public surface contract
- `docs/integrations/` — per-adapter integration guides
