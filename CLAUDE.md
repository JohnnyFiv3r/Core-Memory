# CLAUDE.md — Core Memory

Canonical reference: `docs/architecture_overview.md`
Phase completion state: `docs/status.md`
Cleanup workstream: `docs/cleanup-plan.md` + `docs/PRD/README.md`
Compatibility ledger: `docs/compatibility_ledger.md`

---

## What this repo is

Core Memory is the **causal memory layer** for AI agents. It is not a vector search
engine, not a graph database, and not a framework. It composes those things as
pluggable tiers. The datastore, semantic index, and all framework integrations
(MCP, PydanticAI, OpenClaw, LangChain, CrewAI, SpringAI) are adapters — none
are first-party.

---

## Guiding principle — engineering simplicity

There is a real risk that Core Memory / Satorid becomes over-complex at the
storage and retrieval layer. The danger is not that the project reduces to
"a flat memory file plus semantic search" — it is that we solve several
distinct problems at once and describe all of them as "memory."

The distilled question every feature must answer:

> **What must remain true across agents, sessions, and changing evidence that
> ordinary retrieval cannot reliably preserve?**

A flat file and semantic search already cover 80–90% of practical value:
preserving facts, preferences, decisions, and conventions; retrieving relevant
prior context; maintaining project summaries; sharing team knowledge through
Git; providing provenance through links; superseding old information with
simple status fields; generating a current "working memory" document. A
well-maintained MEMORY.md, event log, embeddings index, and periodic synthesis
pass outperforms many elaborate memory systems.

That approach breaks — and Core Memory is justified — only where memory
becomes **contested, temporal, distributed, and action-relevant**:

- Is this statement observed, inferred, assumed, or merely repeated?
- Was it true then, is it true now, and what caused it to change?
- Which source should dominate when memories conflict?
- Is this a user belief, an organizational policy, an agent conclusion, or an
  external fact?
- What did the system know at the moment a decision was made?
- Which agent altered the shared understanding, and on what evidence?
- Can a new agent inherit not just conclusions, but the constraints and
  reasoning boundaries around them?
- Can we safely revise a belief without silently rewriting history?

Semantic search retrieves passages resembling a query; it does not solve any
of those questions. The essential product is therefore not "better memory" —
it is **a governed continuity layer for agents**: preserving claims, evidence,
change, and identity across tools and time. Put most simply: *Satorid prevents
shared agent context from becoming an unauditable pile of text.*

### Boring primitives, rich views

A good architecture has a small number of boring primitives from which richer
interpretations are computed. Core Memory should need only:

1. **Events** — something happened or was said
2. **Claims** — a proposition extracted from an event
3. **Evidence links** — what supports or contradicts a claim
4. **Subjects / scopes** — who or what the claim concerns
5. **Validity state** — current, superseded, disputed, provisional
6. **Provenance** — source, actor, timestamp, confidence
7. **Policies** — rules determining what may be promoted into working context

Nearly everything else is a **projection**, not a primitive:

- A *worldline* is the ordered history of claims about an entity
- A *tension* is two simultaneously active incompatible claims
- A *storyline* is a clustered event sequence
- A *self-model* is a generated view over identity-relevant claims
- A *causal surface* is a graph query or visualization
- *Myelination* is retrieval weight derived from repetition, confirmation,
  and use

When adding features, implement rich semantics as **views computed over the
primitives** — never as new first-class storage concepts.

### The flat-file test

Force every major feature through this test before building it:

> **Could this be implemented adequately with Markdown, metadata, embeddings,
> and a periodic summarizer?**

If yes, it should probably begin life that way. Only promote it to a
first-class subsystem when the flat-file version demonstrably fails one of the
contested/temporal/distributed/action-relevant questions above.

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

Do not add flat `.py` files beyond the sanctioned sets below. CLI code goes in
`core_memory/cli/` (handlers, parsers, compat). Runtime concerns go in the
relevant `runtime/` subpackage (`turn/`, `flush/`, `session/`, `passes/`,
`queue/`, `observability/`, `dreamer/`, `ingest/`). OpenClaw integration code
goes in `integrations/openclaw/`.

Sanctioned at `core_memory/` root: `__init__.py`, `_version.py`, `memory.py`,
`transcript_ingest.py`, `identifiers.py`, `llm_client.py`, `provider_config.py`.
Sanctioned at `runtime/` root: `__init__.py`, `engine.py`, `state.py`,
`event_schemas.py` as a compatibility import path for
`core_memory.schema.event_schemas`. No runtime root relocation debt should be
added; see `docs/compatibility_ledger.md` and
`scripts/architecture_guards_baseline.json`.

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
(no more `openclaw_flags.py`). Event schema constants live in
`core_memory/schema/event_schemas.py`; `core_memory/runtime/event_schemas.py`
remains only as a compatibility import path. Do not add new imports that route
runtime or persistence code through `integrations/openclaw/`.

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
| SOUL surfaces | Shipped governed/read surfaces; host self-model remains external |

---

## Cleanup workstream phase summary

| Phase | Topic | Status |
|---|---|---|
| 0 | CI baseline + coverage | Complete |
| 1 | Dead file removal | Classified retained compatibility; proven-dead files retired |
| 2 | Circular import fixes | Complete |
| 3 | PydanticAI + adapter boundary | Complete |
| 4 | Classify `graph/api.py` compat facade | Complete at architecture layer; retained public compatibility backlog |
| 5 | Persistence delegation flatten | MRO flat; legacy mixin artifacts retired |
| 6 | Storage adapter capability tiers | Complete |
| 7 | Graph backend abstraction (Neo4j, Graphiti, Obsidian, plugin API) | Complete (7a–7i done) |
| 8 | `core-memory init` wizard + doctor | Complete (8a–8b done) |
| 9 | Structural consolidation (runtime/, cli/, openclaw/) | Complete at architecture layer; retained public compatibility backlog |
| 10 | Documentation consolidation | Complete (10a–10g done) |

Architecture cleanup baseline: zero known guard debt. Remaining public
compatibility surfaces are governed by `docs/compatibility_ledger.md` as a
post-cleanup deprecation backlog.

See `docs/status.md` for current completion state and open items.
See `docs/cleanup-plan.md` for sequence, prerequisites, and per-step details.
See `docs/PRD/README.md` for all task specs.
