# CLAUDE.md — Core Memory

Canonical reference: `docs/architecture_overview.md`
Phase completion state: `docs/status.md`
Cleanup workstream: `docs/cleanup-plan.md` + `docs/PRD/README.md`

---

## What this repo is

Core Memory is the **causal memory layer** for AI agents. It is not a vector search
engine, not a graph database, and not a framework. It composes those things as
pluggable tiers. The datastore, semantic index, and all framework integrations
(MCP, PydanticAI, OpenClaw, LangChain, CrewAI, SpringAI) are adapters — none
are first-party.

---

## Architectural invariants — never violate these

### 1. Layering law — dependencies flow downward only

```
schema → persistence → domain logic → retrieval → runtime → integrations
```

- `schema/`, `temporal/` import nothing from this repo
- `persistence/` imports only `schema/`
- `claim/`, `entity/`, `association/`, `graph/` import only `schema/` + `persistence/`
- `retrieval/` imports domain logic + persistence, not runtime
- `runtime/` imports retrieval and below, not integrations
- `integrations/` consume only `core_memory/__init__.py` (the public API surface)

**Never import upward.** An import from `persistence/` into `schema/` is a bug.
An import from `runtime/` into an integration is a bug in the other direction.

### 2. All frameworks are equal adapters

No adapter gets privileged access to internal modules. OpenClaw is the original
testbed; Phase 9 unwound its over-coupling and isolated it in
`integrations/openclaw/`. **Do not deepen OpenClaw coupling.** Do not add new
imports of `integrations.openclaw.*` modules from anywhere outside `integrations/`.

### 3. Retrieval happens every turn — always

Every agent turn executes the retrieval pipeline. The rolling window check is
**tier 1**, not a bypass condition. Tiers are walked cheapest-first:

1. Rolling window / current session lookup (always tier 1)
2. Semantic candidate retrieval (entity registry + semantic index)
3. Causal reasoning — walk causal edges from candidate seeds
4. Full transcript hydration — recover cited turns for source-grounded answers

The tier where retrieval stops varies per query. The pipeline always starts.

### 4. Association crawler is always agent-judged

The association crawler runs at `agent_end` every turn. Causal links are never
inferred automatically — they are always the result of an agent judgment call.
Do not add code that writes associations without an agent-issued decision.

### 5. Write pipeline entry point is `emit_turn_finalized`

The canonical write path is:
```
emit_turn_finalized → process_turn_finalized → write bead → crawl associations → promote
```

Do not write beads by calling persistence directly from integrations. Use the
public API.

### 6. No new flat files at `core_memory/` root or `runtime/` root

`core_memory/` root and `runtime/` root are now clean (Phase 9 complete). Do not
re-introduce flat `.py` files. CLI code goes in `core_memory/cli/` (handlers,
parsers, compat). Runtime concerns go in the relevant `runtime/` subpackage
(`turn/`, `flush/`, `session/`, `passes/`, `queue/`, `observability/`,
`dreamer/`). OpenClaw integration code goes in `integrations/openclaw/`.

---

## Write side (per turn, at agent_end)

1. Write full bead for this turn
2. Crawl for associations to prior beads (agent-judged)
3. Extract claims; resolve against current truth
4. Promote bead or mark as promotion candidate

Entry: `emit_turn_finalized` → `process_turn_finalized`

## Session boundaries

- **Session start:** inject rolling window beads as background context
- **Session end / compaction:** archive promoted beads (full content + associations);
  compress non-promoted beads to compact form (type / title / associations)
- **Memory flush:** update rolling window for next session

---

## OpenClaw coupling — resolved (Phase 9 complete)

The OpenClaw integration is now isolated in `core_memory/integrations/openclaw/`
like any other adapter. Generic feature flags live in `core_memory/config/feature_flags.py`
(no more `openclaw_flags.py`). Runtime event schemas live in
`core_memory/runtime/event_schemas.py` as constants. Do not add new imports that
route runtime or persistence code through `integrations/openclaw/`.

---

## Public API surface

Consumers (integrations, tests, CLI) import only from `core_memory/__init__.py`.
Key symbols: `recall`, `emit_turn_finalized`, `process_turn_finalized`,
`process_session_start`, `process_flush`, `memory_search`, `memory_trace`,
`memory_execute`, `StorageBackend`, `JsonFileBackend`, `SqliteBackend`,
`create_backend`, `Turn`, `Bead`, `Association`, `EvidenceItem`, `RecallResult`.

---

## Storage

`StorageBackend` protocol lives in `persistence/backend.py`. `JsonFileBackend`
(default, zero deps) and `SqliteBackend` ship. `create_backend()` is the factory;
reads `CORE_MEMORY_BACKEND` env var. Phase 6 adds `BackendCapabilities` and
extends the protocol with `search_candidates()`, `traverse()`,
`hydrate_turn_refs()`.

---

## Active subsystems

| Subsystem | Status |
|---|---|
| Bead schema, turn ingestion, persistence | Core / always on |
| Association crawler | Core / always on |
| Claim extraction | Core / always on |
| Entity registry | Core / always on |
| Rolling window / compaction | Core / always on |
| Retrieval pipeline | Core / always on |
| Semantic indexing (FAISS / pgvector) | Opt-in |
| Dreamer ("Move 37") | Background, opt-in |
| Myelination | In active development |
| SOUL.md | Future / emerging |

---

## Cleanup workstream phase summary

| Phase | Topic | Status |
|---|---|---|
| 0 | CI baseline + coverage | Complete |
| 1 | Dead file removal | Complete |
| 2 | Circular import fixes | Complete |
| 3 | PydanticAI + adapter boundary | Complete |
| 4 | `graph/api.py` compat facade removal | Complete |
| 5 | Persistence delegation flatten | Complete |
| 6 | Storage adapter capability tiers | Complete |
| 7 | Graph backend abstraction (Neo4j, Graphiti, Obsidian, plugin API) | Complete (7a–7i done) |
| 8 | `core-memory init` wizard + doctor | Complete (8a–8b done) |
| 9 | Structural consolidation (runtime/, cli/, openclaw/) | Complete (9a–9h done) |
| 10 | Documentation consolidation | Complete (10a–10g done) |

See `docs/status.md` for current completion state and open items.
See `docs/cleanup-plan.md` for sequence, prerequisites, and per-step details.
See `docs/PRD/README.md` for all task specs.
