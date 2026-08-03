# Technical Execution Plan: Core Memory Observation Ledger Architecture

## From the Approved Observation-Ledger PRD to a Production Cutover

- **Status:** Ready for implementation
- **Governing product document:** `docs/PRD/observation-ledger-architecture.md`
- **Code baseline:** `origin/master@0257cf11e23ce2ec7fd08590e10df00d67402d20`
- **Prepared:** 2026-08-03
- **Program shape:** 11 program stages (0-10), 90 PR-sized execution phases
- **Delivery rule:** one execution phase = one complete implementation push =
  one independently mergeable pull request
- **Primary release unit:** one tenant-scoped SQL ledger with one semantic
  runtime, one write pipeline, one revision resolver, one artifact lifecycle,
  one retrieval pipeline, and one job protocol

---

## 0. Purpose and Authority

This document is the technical implementation plan for the approved Core
Memory Observation Ledger Architecture PRD. The PRD remains authoritative for
product behavior, governing invariants, semantic policy, and acceptance
criteria. This plan makes those decisions executable by specifying:

- dependency order;
- target package and database boundaries;
- the exact responsibilities of each architectural workstream and atomic PR
  phase;
- changes to current runtime components;
- migration and compatibility sequencing;
- test, observability, rollout, rollback, and deletion gates;
- the proof required before the next stage may begin.

If this plan conflicts with the PRD, the PRD wins. If implementation uncovers
a genuine product contradiction, work stops at that boundary and the PRD is
amended explicitly. Implementation convenience must not silently weaken a
governing invariant.

This plan deliberately uses small pull requests. It does **not** mean the old
and new architectures may coexist indefinitely. Compatibility exists only as
a bounded migration mechanism. Each compatibility component is created with a
named deletion phase and an executable deletion gate.

### 0.1 Baseline discipline

Implementation begins in a clean worktree created from the verified remote
baseline above, or from its current reviewed successor. The implementation
owner records the actual base commit in the first pull request and rebases the
plan if material architecture has changed.

The current checkout is not the implementation baseline. It may contain
unrelated work and must not be cleaned, reset, or repurposed to begin this
program.

### 0.2 Plan-level decisions

The following implementation choices are fixed by this plan unless benchmark
or contract-test evidence requires an explicit amendment:

1. **No ORM in the canonical ledger.** Use a small SQL adapter over Python's
   `sqlite3` locally and `psycopg` for PostgreSQL. SQL is versioned, inspectable,
   and shared as closely as the engines allow.
2. **Typed relational identity, authority, lifecycle, and time columns.** Use
   constrained JSON only for operation-specific content that genuinely varies
   by semantic object kind. Fields used for isolation, ordering, joins,
   revisions, validity, state, or idempotency are never buried in JSON.
3. **Opaque UUIDv4 identifiers in v1.** IDs carry no semantic meaning. Every
   canonical row also receives a tenant-scoped, monotonically increasing
   `ledger_position` in its append transaction.
4. **Append-only canonical tables; mutable operational tables.** Jobs, leases,
   projection watermarks, caches, and migration checkpoints may update.
   SourceEvents, semantic rows, receipts, revisions, and evidence records may
   not update or delete through application roles.
5. **One local and hosted protocol.** SQLite and PostgreSQL implement the same
   `Ledger`, `JobStore`, and projection contracts. Local in-process workers are
   allowed only through the same job leasing and handler protocol.
6. **Partial optional-row policy.** A valid bead may commit when an optional
   assertion or association row is invalid. Every invalid optional row is
   represented in the semantic receipt and schedules a typed repair job. A
   missing or invalid bead invalidates the annotation result and commits no
   semantics.
7. **No permanent dual write.** Legacy import, controlled replay, and
   read-comparison are allowed. A request never treats JSONL and SQL as two
   simultaneous canonical write authorities.
8. **Projections are disposable.** Vector, graph, current-state, hot-context,
   and SOUL materializations expose watermarks and rebuild from canonical SQL.
9. **Compatibility translates calls, not semantics.** Legacy API adapters may
   create SourceEvents or invoke the new retrieval service. They may not retain
   old fallback generation, mutation, or resolver behavior.
10. **Thresholds come from the Stage 0 baseline.** This plan fixes invariant
    gates immediately and fixes quality thresholds only after the versioned
    benchmark has been run and reviewed.

### 0.3 One phase, one complete PR

The 0-10 groupings in this document are **program stages**, not pull-request
units. The authoritative execution phases are the `PR-00A` through `PR-10N`
entries in Section 6.5. Each execution phase has exactly one branch, one
complete implementation push, one pull request, one acceptance decision, and
one merge.

An execution phase must include all implementation, migrations, tests,
observability, documentation, compatibility handling, and rollback behavior
needed for its stated outcome. A phase cannot merge as scaffolding with its
working behavior deferred to a later PR. A later phase may build on a completed
contract, but it cannot be required to make the earlier phase truthful.

Dependent PRs are not stacked on unmerged branches. Each phase starts from the
latest main branch after all declared dependencies have merged. Research,
fixture preparation, and design review may happen in parallel, but the delivery
unit remains one complete PR.

The planned workflow is:

```text
sync latest main
  -> create phase branch
  -> implement the entire phase locally
  -> run every phase gate
  -> make one complete implementation push
  -> open one PR
  -> address review within that PR if necessary
  -> merge
  -> begin dependent phase from updated main
```

If implementation discovery shows that a listed phase is too large for one
reviewable PR, work stops before a partial push. The plan is amended to split
that phase into new atomic phase IDs with explicit independent outcomes and
dependencies. It is never handled through a partial PR, an unreviewable large
PR, or a promise to finish the phase in follow-up work.

---

## 1. Execution Principles

### 1.1 The unit of delivery is proved behavior

A PR phase is complete only when it includes implementation, tests,
observability, documentation of its public contract, and proof against both
SQLite and PostgreSQL where persistence is involved. A merged schema without a
working transaction path is not a completed ledger milestone. A passing mock
test without a live-model canary is not proof of semantic quality.

### 1.2 Mechanical and semantic authority remain separate

Deterministic code may:

- authenticate and authorize callers;
- assign IDs, source identity, system time, and ledger positions;
- validate schemas, evidence coordinates, vocabulary membership, and referential
  integrity;
- collect neutral candidates without promoting them to meaning;
- enforce idempotency and append transactions;
- schedule, lease, retry, and reconcile work;
- pack an LLM-authored rank order into a token budget;
- compute inverse traversal from a canonical predicate;
- materialize and rebuild projections;
- verify that cited IDs and spans exist.

Deterministic code may not author or infer:

- a bead label, title, or summary;
- whether an assertion exists;
- claim meaning or claim update action;
- association predicate, direction, temporal meaning, or causal status;
- goal, storyline, Dreamer, lesson, principle, identity, value, tension, or
  SOUL content;
- promotion value or context-relative semantic importance;
- retrieval intent, relevance, sufficiency, answer, or semantic abstention.

Invalid or unavailable semantic output remains pending, retryable, failed, or
quarantined. It is never replaced with deterministic meaning.

### 1.3 Add the target, redirect callers, delete the predecessor

Every major subsystem follows the same three-stage sequence:

1. add the target contract and implementation behind an explicit service port;
2. redirect all production callers and prove parity or intended behavior change;
3. delete the predecessor, its flags, its state, and its tests.

Large file moves are deferred until the target boundary works. New code is not
implemented inside legacy authority modules such as
`core_memory/persistence/backend.py`; that module remains a migration source
until deletion.

### 1.4 No false green state

Every API and operational surface distinguishes at least:

- `captured`: SourceEvent and mandatory annotation job committed;
- `pending`: semantic completion has not produced a valid bead;
- `completed`: valid semantic records committed;
- `completed_with_repairs`: the bead committed and optional children need
  semantic repair;
- `retrying`: a retryable semantic or operational failure exists;
- `failed`: retry policy exhausted and operator or new evidence is required;
- `quarantined`: input or legacy authority is unsafe to promote automatically.

Exit code zero, a completed job row, or a projection write does not by itself
prove semantic completion.

---

## 2. Architecture Workstream Dependency Graph

This graph shows capability-level dependencies between the broader `EP-*`
workstreams. It does not define PR boundaries. The exact merge dependency for
each complete PR is authoritative in Section 6.5.

```text
EP-00A architecture contract ─┬─> EP-01A domain kernel
                              ├─> EP-00B evaluation corpus
                              └─> EP-00C legacy inventory

EP-01A ─> EP-01B SQL schema ─> EP-01C SQLite ledger ─┐
                              └> EP-01D PostgreSQL ───┼─> EP-01E jobs/worker
EP-00B ───────────────────────────────────────────────┘

EP-01E ─> EP-02A semantic contracts ─> EP-02B runtime/adapters
                                     └> EP-02C verifier/no-fallback gate

EP-02C ─> EP-03A SourceEvent capture ─> EP-03B annotation commit
                                      └> EP-03C adapter convergence

EP-03B ─┬─> EP-04A revision resolver ─> EP-04B claims ─> EP-04C temporal APIs
        ├─> EP-05A association candidates ─> EP-05B review ─> EP-05C evidence/coverage
        └─> EP-06A artifact kernel

EP-04C + EP-05C + EP-06A ─> EP-06B Dreamer ─┬─> EP-06C goals/storylines
                                             └─> EP-06D SOUL

EP-03C + EP-05C ─> EP-07A promotion ─> EP-07B packing/unpack

EP-04C + EP-05C + EP-07B ─> EP-08A retrieval planning/collectors
                           ─> EP-08B resolution/expansion
                           ─> EP-08C hydration/judgment
                           ─> EP-08D synthesis/API convergence

EP-06D + EP-07B + EP-08D ─> EP-09A projections
                           ─> EP-09B worker consolidation
                           ─> EP-09C configuration/integration cleanup

EP-00C + EP-09C ─> EP-10A importer ─> EP-10B shadow verification
                                    ─> EP-10C canary/cutover
                                    ─> EP-10D legacy deletion
```

### 2.1 Safe parallelism

The following lanes may prepare independent complete phases concurrently after
their prerequisites merge. No dependent branch is stacked, and each row in
Section 6.5 still ships as its own complete PR:

| Window | Lane A | Lane B | Lane C |
|---|---|---|---|
| Foundation | Domain/schema/ledger | Evaluation corpus | Legacy inventory |
| Semantics | Runtime/provider adapters | Live evaluation harness | Architecture checks |
| Meaning layers | Claims/resolver | Associations/evidence | Artifact kernel |
| Consumer layers | Artifacts | Promotion | Retrieval collectors |
| Closure | Projection/worker consolidation | Migration rehearsals | Quality/canary reporting |

Schema ownership remains serialized. Only one open PR phase at a time may
change canonical table definitions. Dependent implementation PRs are never
stacked; they begin after their prerequisites merge to main.

### 2.2 Critical path

The minimum production path is:

```text
domain -> SQL ledger -> jobs -> semantic runtime -> observation write
-> revision resolver -> association evidence -> unified retrieval
-> projection/worker consolidation -> migration -> deletion
```

Artifact and promotion work can overlap after the observation and association
foundations, but broad cutover cannot occur until they use the unified systems.

---

## 3. Target Technical Architecture

### 3.1 Package layout

The program converges on this package structure without requiring an early
big-bang move:

```text
core_memory/
  domain/
    ids.py
    envelope.py
    events.py
    evidence.py
    beads.py
    assertions.py
    revisions.py
    artifacts.py
    context.py
    vocabulary.py
    resolver.py

  ledger/
    protocol.py
    transactions.py
    errors.py
    sqlite.py
    postgres.py
    queries.py
    backup.py
    schema/
      sqlite/
      postgres/
    migrations/

  semantic/
    contracts.py
    runtime.py
    routing.py
    validation.py
    receipts.py
    providers/
    operations/
      annotate_event.py
      revise_assertion.py
      review_associations.py
      synthesize_artifact.py
      review_artifact.py
      assemble_context.py
      plan_retrieval.py
      judge_evidence.py
      synthesize_answer.py
      verify_output.py

  jobs/
    model.py
    store.py
    registry.py
    worker.py
    dependencies.py
    reconciliation.py
    handlers/

  projections/
    base.py
    registry.py
    current_state.py
    search.py
    graph.py
    hot_context.py
    soul.py

  retrieval/
    models.py
    pipeline.py
    collectors.py
    expansion.py
    hydration.py
    citations.py

  api/
    service.py
    receipts.py
    errors.py

  integrations/
    ... adapters only ...
```

### 3.2 Dependency rules

Allowed import direction:

```text
domain
  <- ledger protocols and implementations
  <- semantic, jobs, projections, and retrieval services
  <- API service
  <- integration adapters and HTTP/CLI transports
```

Forbidden imports are enforced in CI:

- `domain` imports no runtime, persistence, HTTP, integration, provider, or
  projection module;
- canonical ledger adapters import no semantic operation or integration;
- semantic operations depend on domain types and injected ports, never concrete
  databases;
- projections cannot be imported by the canonical writer to decide truth;
- integrations cannot import internal stores, legacy queues, or feature-specific
  generators;
- no new module outside the migration package reads JSONL or
  `.beads/index.json`.

### 3.3 Runtime topology

```text
host / HTTP / SDK
      |
      v
CoreMemoryService.observe(SourceEventInput)
      |
      +-- transaction: SourceEvent + annotate_event Job
      |
      v
one JobStore / worker registry
      |
      v
SemanticRuntime.execute(ANNOTATE_EVENT)
      |
      +-- LLM multi-step reasoning
      +-- deterministic contract/evidence validation
      +-- optional independent semantic verification
      |
      v
transaction: bead + optional assertions + receipt + dependent Jobs
      |
      +--> current-state projection
      +--> association review
      +--> artifact policies
      +--> promotion/context
      +--> search/graph projections
      |
      v
one RetrievalPipeline -> cited answer or explicit abstention
```

### 3.4 Local and hosted deployment

| Concern | Local | Hosted |
|---|---|---|
| Canonical database | SQLite WAL | PostgreSQL |
| Tenant isolation | mandatory tenant predicates | RLS plus application predicates |
| Worker | optional in-process loop or separate process | horizontally replicated process |
| Leasing | transactional row claim | `FOR UPDATE SKIP LOCKED` |
| Search projection | local vector/index adapter | pgvector or configured vector adapter |
| Graph projection | rebuildable local adjacency/index | rebuildable graph adapter |
| Backup | SQLite online backup + manifest | PostgreSQL backup/PITR policy |
| Semantic provider | inline or delegated adapter | routed provider/delegated adapter |

Local operation is not a different architecture. The same job states,
idempotency keys, receipts, handlers, and acceptance tests apply.

---

## 4. Canonical Persistence Design

### 4.1 Ledger envelope

Every canonical table includes, directly or through a strict one-to-one
envelope row:

```text
id                  text/uuid primary key
tenant_id           text/uuid not null
workspace_id        text/uuid null
ledger_position     bigint not null
recorded_at         timestamptz/text not null
actor_kind          constrained text not null
actor_id            text null
operation_id        text/uuid null
schema_version      integer not null
provenance_class    constrained text not null
```

`ledger_position` is unique within a tenant. It represents append order, not
semantic superiority and not valid time.

### 4.2 Position allocation

Use `ledger_counters(tenant_id, next_position)`:

- PostgreSQL allocates with a single `INSERT ... ON CONFLICT ... DO UPDATE ...
  RETURNING` inside the append transaction;
- SQLite uses `BEGIN IMMEDIATE`, reads or creates the tenant counter, increments
  it, and appends before commit;
- a transaction that rolls back may leave no row and no externally visible
  position; gaps are allowed but duplicate positions are not;
- callers cannot supply a ledger position.

### 4.3 Logical tables

Canonical tables:

```text
source_events
source_event_payloads
evidence_refs
observation_beads
claim_assertions
association_assertions
association_review_decisions
revisions
evidence_sets
evidence_set_members
artifacts
artifact_reviews
context_view_revisions
semantic_task_receipts
semantic_task_attempts
migration_provenance
```

Operational and projection tables:

```text
jobs
job_attempts
job_dependencies
job_dead_letters
projection_watermarks
current_object_state
association_coverage
search_projection_entries
graph_projection_entries
hot_context_projection
soul_render_projection
source_hydration_cache
migration_checkpoints
```

### 4.4 Table modeling rule

Use typed columns for:

- ownership and tenancy;
- source identity and idempotency;
- object kind and lineage;
- evidence foreign keys and coordinates;
- assertion subject, predicate, object/value, polarity, and scope;
- association endpoints and canonical predicate;
- valid-time interval;
- revision predecessor, successor, action, and scope key;
- artifact kind, state, lineage, and review requirement;
- association-review candidate identity and accepted/rejected/deferred decision;
- artifact-review target, decision, reviewer authority, and policy version;
- task kind, state, route, attempt, and error class;
- job kind, eligibility, lease, dependency, and terminal state.

Use canonical JSON for:

- the immutable normalized source payload;
- bead facets and small structured qualifiers;
- assertion value variants not covered by typed scalar columns;
- artifact-kind-specific content validated by a versioned content schema;
- LLM operation inputs/outputs after secret and source-minimization policy;
- model/provider metadata and token/cost usage;
- retrieval plans, evidence judgments, and answer metadata.

JSON serialization must sort keys, reject non-finite numbers, preserve Unicode,
and use explicit schema versions. JSON equality is never used to determine
semantic equivalence.

### 4.5 Immutability

PostgreSQL:

- application roles receive `SELECT` and `INSERT` only on canonical tables;
- `BEFORE UPDATE OR DELETE` triggers reject mutations even if grants drift;
- a separate maintenance role exists for audited privacy erasure and disaster
  repair only;
- current-state flags live in projection tables.

SQLite:

- `BEFORE UPDATE` and `BEFORE DELETE` triggers abort canonical mutations;
- the application does not expose generic SQL execution;
- maintenance erasure opens the database through an explicit audited tool,
  performs scoped deletion, and forces all projections to rebuild.

### 4.6 Transaction boundaries

The following are indivisible:

1. SourceEvent append plus mandatory `annotate_event` job;
2. valid annotation bundle append plus semantic receipt plus all mandatory
   dependent jobs;
3. new assertion/artifact/context revision plus Revision record plus required
   projection jobs;
4. accepted association review plus coverage record plus graph/search jobs;
5. job completion plus creation of statically known dependent jobs.

LLM calls never occur while a database write transaction is held open.
Semantic work follows read snapshot -> model call -> validation -> short append
transaction, with optimistic precondition checks at commit.

### 4.7 Canonical constraints

Required constraints include:

- unique `(tenant_id, source_kind, source_external_id, idempotency_key)` where
  the source supplies an external ID;
- unique `(tenant_id, source_event_id)` on bead lineage identity;
- unique bead version ID and explicit lineage ID;
- evidence references cannot cross tenant or workspace authorization boundaries;
- assertion and artifact revisions cannot cross kind or scope key;
- association endpoints cannot be equal for predicates that prohibit self-links;
- canonical association predicate is stored once in forward direction;
- valid interval requires `valid_to > valid_from` when both are known;
- job dedupe key unique for all non-terminal-equivalent obligations;
- receipt operation ID plus attempt number unique;
- projection watermark unique by tenant, projection kind, and schema version.

### 4.8 Query consistency classes

Each read API declares one class:

- `canonical`: read ledger/current resolver directly; no projection dependency;
- `bounded_stale`: projection allowed only within configured watermark lag;
- `eventual`: projection may lag and response returns the watermark;
- `fresh_or_pending`: wait up to a caller budget, then return pending honestly.

Current claim truth, revision chains, bead archive lookup, and authorization are
always canonical. Search ranking, graph traversal acceleration, hot-context
rendering, and SOUL files may use bounded-stale projections.

---

## 5. Service Contracts

### 5.1 `Ledger`

The canonical persistence port exposes intention-revealing methods, not a
generic repository:

```python
class Ledger(Protocol):
    def capture_event(self, command: CaptureEvent) -> CaptureReceipt: ...
    def commit_annotation(self, command: CommitAnnotation) -> SemanticReceipt: ...
    def append_revision(self, command: AppendRevision) -> RevisionReceipt: ...
    def append_association_review(self, command: CommitAssociationReview) -> None: ...
    def append_artifact(self, command: CommitArtifact) -> ArtifactReceipt: ...
    def append_context_revision(self, command: CommitContextRevision) -> None: ...
    def get_event(self, ref: ObjectRef) -> SourceEvent: ...
    def get_bead(self, ref: ObjectRef, *, as_of: AsOf | None = None) -> ObservationBead: ...
    def load_revision_scope(self, scope: ScopeKey, *, as_of: AsOf | None = None) -> RevisionScope: ...
    def scan_after(self, tenant_id: str, position: int, *, kinds: set[str]) -> Iterator[LedgerRow]: ...
```

No public `save`, `update`, `delete`, or `upsert` method exists for canonical
semantics.

### 5.2 `JobStore`

```python
class JobStore(Protocol):
    def enqueue(self, command: EnqueueJob) -> JobRef: ...
    def lease(self, request: LeaseRequest) -> list[LeasedJob]: ...
    def heartbeat(self, lease: LeaseToken) -> None: ...
    def succeed(self, command: CompleteJob) -> None: ...
    def retry(self, command: RetryJob) -> None: ...
    def fail(self, command: FailJob) -> None: ...
    def release_expired(self, now: datetime) -> int: ...
    def reconcile(self, request: ReconcileRequest) -> ReconcileReport: ...
```

Handlers return typed outcomes. They do not update job rows directly.

### 5.3 `SemanticRuntime`

```python
class SemanticRuntime(Protocol):
    def execute(self, request: SemanticTaskRequest[TInput]) -> SemanticTaskResult[TOutput]: ...
```

The request always carries:

- task kind and version;
- tenant/workspace routing context;
- authorized EvidenceRefs and minimized source payload;
- expected output schema and vocabulary version;
- prompt and policy version;
- idempotent operation ID;
- effort, cost, latency, and retry budgets;
- verification policy;
- no semantic fallback field.

The result always carries either a validated semantic output or a typed failure.
Every provider attempt becomes an immutable receipt, including invalid outputs
and correction attempts.

### 5.4 `CurrentStateResolver`

```python
class CurrentStateResolver(Protocol):
    def resolve(self, scope: RevisionScope, *, as_of: AsOf | None = None) -> Resolution: ...
```

The resolver is pure: no I/O, clocks, ranking models, mutable caches, or
latest-row shortcuts. It returns one of:

```text
current | ambiguous | contested | retracted | absent | invalid
```

### 5.5 `RetrievalPipeline`

```python
class RetrievalPipeline(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse: ...
```

One orchestrator owns planning, neutral collection, resolution, expansion,
hydration, evidence judgment, bounded iteration, synthesis, abstention, and
citation verification. Collectors provide candidates; they do not decide
truth or answer text.

---

## 6. Pull Request and Branch Strategy

### 6.1 Naming

Use one branch per execution phase:

```text
codex/observation-ledger-pr-00a
codex/observation-ledger-pr-01d
codex/observation-ledger-pr-08f
```

PR titles begin with the execution-phase ID. Example:

```text
[PR-03C] Commit evidence-grounded annotation bundles
```

### 6.2 PR size

Every phase is pre-sized to these targets:

- 200-800 net implementation lines for contract or behavior PRs;
- 800-1,500 lines only where SQL dialects and tests necessarily duplicate
  structure;
- one canonical schema migration concern per PR;
- no unrelated formatting or file moves;
- no mixed subsystem addition and predecessor deletion unless deletion is the
  complete stated outcome of that phase;
- one complete implementation push after local gates pass;
- one PR whose acceptance criteria are satisfied without a dependent unmerged
  PR.

No stacked PRs and no partial phase pushes are planned. If a phase exceeds
these bounds, revise this document first and replace it with two or more new
atomic phase IDs. Each replacement phase must still have a complete,
independently testable outcome.

### 6.3 Required PR description

Every PR records:

1. governing PRD invariants;
2. behavior added or redirected;
3. old authority still active;
4. new tables, jobs, metrics, and configuration;
5. tests run against SQLite, PostgreSQL, recorded semantics, and live semantics;
6. migration and rollback behavior;
7. follow-up deletion phase;
8. proof artifacts and known limitations.

### 6.4 Merge discipline

- Schema and public-contract PRs require two approvals.
- Architecture-boundary and invariant tests are required checks.
- Recorded semantic fixtures cannot be regenerated in the same PR that changes
  the evaluated prompt without a human-readable delta report.
- Live-model canaries are required before a semantic PR phase is declared
  production-ready, even if they are not required on every commit.
- A compatibility switch must default to the old path only until its target
  cutover phase; after cutover it defaults to the new path and has a dated
  deletion phase.
- A PR is opened only after its complete planned implementation has been pushed.
  Review corrections remain in the same PR, but unimplemented planned scope
  cannot be deferred to a follow-up.
- Dependent phase branches start from main only after prerequisite PRs merge.

### 6.5 Authoritative atomic execution-phase register

This register, rather than the broader `EP-*` architectural workstreams in
Sections 7-17, defines delivery units. Each row is exactly one complete PR. The
workstream column points to the detailed requirements that apply to the phase.
All phases begin in `not started` state. Dependency cells omit the `PR-` prefix
only for compactness; for example, `01A` means `PR-01A` and `01A-01C` means all
three already-merged phases in that inclusive range.

#### Stage 0 — Contract, evaluation, and inventory

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-00A` | Architecture contract, guard v2, fingerprinted expiring exceptions, parent register, and violation self-tests | — | required structural check rejects new/widened exceptions, known mutation drift, named fallbacks, and expired debt | EP-00A |
| `PR-00B` | Observation Ledger benchmark package, CLI, schemas, user-selected author/judge model contracts, run states, and report contracts | 00A | provider-neutral fixture run and contamination self-tests are reproducible | EP-00B |
| `PR-00C` | Read-only static code and filesystem authority inventory | 00B | classified paths, symbols, counts, hashes, and safety tests reconcile | EP-00C |
| `PR-00D` | Read-only SQLite/PostgreSQL/hosted inventory and consolidated legacy manifest | 00C | every known authority is accessible and classified without mutation | EP-00C |
| `PR-00E` | 25 statement/request observation cases with accepted adjudications | 00D | complete inputs, gold, evidence, adjudications, checksums, and tests | EP-00B |
| `PR-00F` | 25 goal/decision observation cases with accepted adjudications | 00E | complete corpus slice and focused validation | EP-00B |
| `PR-00G` | 25 action/result observation cases with accepted adjudications | 00F | complete corpus slice and focused validation | EP-00B |
| `PR-00H` | 25 evidence/reflection observation cases with accepted adjudications | 00G | 100-case observation breadth is complete | EP-00B |
| `PR-00I` | 25 low-semantic/noise/adversarial observation cases with accepted adjudications | 00H | thin-bead and zero-assertion outcomes are represented | EP-00B |
| `PR-00J` | 25 core claim/revision cases with accepted adjudications | 00I | extraction, reaffirmation, supersession, retraction, abstention, and as-of coverage | EP-00B |
| `PR-00K` | 25 adversarial/temporal claim-revision cases with accepted adjudications | 00J | contest, ambiguity, invalid chains, cycles, and bi-temporal coverage | EP-00B |
| `PR-00L` | 25 noncausal association cases with accepted adjudications | 00K | support, contradiction, part-of, similarity, refinement, and no-link coverage | EP-00B |
| `PR-00M` | 25 causal/directional/temporal association cases with accepted adjudications | 00L | cause, enable, block, dependency, resolution, supersession, and time coverage | EP-00B |
| `PR-00N` | 25 association independence/no-link/adversarial cases with accepted adjudications | 00M | direction traps, chronology, independence, correlation, contradiction, and abstention coverage | EP-00B |
| `PR-00O` | 30 unified artifact cases with accepted adjudications | 00N | all ten artifact kinds, revisions, conflicts, and abstention are covered | EP-00B |
| `PR-00P` | 30 promotion/context cases with accepted adjudications | 00O | token pressure, pins, retention, compression, downstream tasks, and unpack are covered | EP-00B |
| `PR-00Q` | 25 semantic/current-state/bi-temporal retrieval cases with accepted adjudications | 00P | semantic and as-of retrieval coverage is complete | EP-00B |
| `PR-00R` | 25 causal/association/hydration retrieval cases with accepted adjudications | 00Q | causal expansion, source hydration, archive unpacking, and evidence coverage | EP-00B |
| `PR-00S` | 25 ambiguity/abstention/citation/injection retrieval cases with accepted adjudications | 00R | adversarial and degraded retrieval coverage is complete | EP-00B |
| `PR-00T` | Public and private live baselines, user-approved thresholds/vocabulary, sanitized private aggregate, and final Stage 0 report | 00S | explicit user-selected models; uncontaminated dual baseline; approved dual gate | EP-00B |

#### Stage 1 — Domain, SQL ledger, and jobs foundation

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-01A` | IDs, tenant/envelope, canonical JSON, vocabulary, and temporal primitives | 00A, 00E-00N | frozen serialization/vocabulary/time tests | EP-01A |
| `PR-01B` | SourceEvent, EvidenceRef, ObservationBead, assertion, and AnnotationBundle domain contracts | 01A | source/evidence/zero-assertion contract tests | EP-01A |
| `PR-01C` | Revision, EvidenceSet, Artifact, ArtifactReview, ContextViewRevision, receipt, and Job domain contracts | 01A, 01B | lineage/scope/serialization tests | EP-01A |
| `PR-01D` | Canonical SQLite/PostgreSQL schema migrations, immutability rules, and schema manifest | 01A-01C, 00D | dual-dialect fresh/incremental schema tests | EP-01B |
| `PR-01E` | Operational jobs/projection schema migrations and checksum/lock migration runner | 01D | migration plan/apply/verify tests | EP-01B |
| `PR-01F` | Complete SQLite Ledger adapter including position allocation, idempotency, backup, and restore | 01D, 01E | shared contract on SQLite plus crash/backup tests | EP-01C |
| `PR-01G` | Complete PostgreSQL Ledger adapter including RLS, roles, positions, and recovery contract | 01D, 01E | shared contract plus RLS/concurrency tests | EP-01D |
| `PR-01H` | JobStore enqueue/lease/heartbeat/complete/retry/fail implementations for both databases | 01E-01G | shared leasing and crash-boundary contract | EP-01E |
| `PR-01I` | Worker registry, typed outcomes, retry policy, local in-process mode, and multi-replica worker | 01H | worker fault/retry/unknown-handler tests | EP-01E |
| `PR-01J` | Versioned obligation catalog, transactional dependency fan-out, and incremental/full reconciler | 01I | injected obligation gaps return to zero | EP-01E / Section 28 |

#### Stage 2 — Semantic operation runtime

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-02A` | Versioned common semantic request/result schemas and operation registry | 01A-01C, 01J | registry/schema/no-fallback contract tests | EP-02A |
| `PR-02B` | Provider protocol, inline/delegated adapters, allowlisted routing, and immutable attempt receipts | 02A | route/tenant/receipt/redaction tests | EP-02B |
| `PR-02C` | Multi-step execution, validation-feedback correction, escalation, retry, and typed failure runtime | 02B | provider/invalid-output fault matrix | EP-02B |
| `PR-02D` | Independent verifier operation, target no-fallback closure, adversarial suite, and live-model smoke | 02C, 00T | live faithfulness/abstention report | EP-02C |

#### Stage 3 — Observation write pipeline

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-03A` | SourceEvent capture service, `/v1/events`, operation status, and truthful capture receipt | 01F-01J, 02A | atomic capture/idempotency tests | EP-03A |
| `PR-03B` | `annotate_event.v1` prompt/schema, evidence validator, and verifier policy | 03A, 02D | greeting/grounding/zero-assertion semantic tests | EP-03B |
| `PR-03C` | Annotation job handler, atomic bundle commit, partial-child repair, receipts, and job fan-out | 03B | dual-database crash/idempotency tests | EP-03B |
| `PR-03D` | Canonical capture adapter protocol plus complete runtime/turn-flow adapter conversion | 03C | runtime/turn callers write only SourceEvents | EP-03C |
| `PR-03E` | Complete hosted/delegated capture and completion adapter conversion | 03D | tenant-route and receipt-convergence canary | EP-03C |
| `PR-03F` | Complete HTTP write endpoint compatibility conversion and deprecation telemetry | 03D, 03E | endpoint contract and no-old-writer tests | EP-03C |
| `PR-03G` | Complete CLI, SDK, and remaining integration-writer conversion with caller inventory closure | 03F | every writer classified; target access counters pass | EP-03C |

#### Stage 4 — Revision and current truth

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-04A` | Pure Revision action matrix and CurrentStateResolver | 01C, 03C | terminal-state/property/cycle tests | EP-04A |
| `PR-04B` | `revise_assertion.v1` semantic operation and verification policy | 04A, 02D | revision action/abstention benchmark | EP-04B |
| `PR-04C` | Claim/Revision ledger commands, API, and rebuildable current-state projection | 04B, 01F, 01G | append flow and direct/projection parity | EP-04B |
| `PR-04D` | Bi-temporal as-of queries, audit response, and all target resolver-call convergence | 04C | valid-time/known-time matrix and single-resolver check | EP-04C |

#### Stage 5 — Associations, coverage, and evidence

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-05A` | Neutral candidate model, collectors, persistence, dedupe, and collection job | 03C, 01J | collectors cannot write relationships | EP-05A |
| `PR-05B` | `review_associations.v1` operation, predicate registry, direction/time/no-link validation | 05A, 02D, 04A | live direction/time/no-link benchmark | EP-05B |
| `PR-05C` | Immutable review decisions, canonical association append/Revision, inverse traversal, API, and re-review | 05B | direction/persistence/retry tests | EP-05B |
| `PR-05D` | EvidenceSet membership, support/contradiction, source-root, independence, and temporal aggregation | 05C, 04D | independence/contradiction property tests | EP-05C |
| `PR-05E` | Association coverage projection, SLO metrics, coverage repair scheduling, and audit API | 05A-05D | eligible/reviewed/stale/gap accounting | EP-05C |
| `PR-05F` | Graph projection adapter, watermarks, inverse query, delete/rebuild, and parity drill | 05C-05E | SQL-only graph rebuild report | EP-05C |

#### Stage 6 — Unified artifacts

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-06A` | Artifact/ArtifactReview ledger commands, lineage/current projection, generic API, and typed-kind registry | 01C, 04D, 05D | append-only lifecycle for two fixture kinds | EP-06A |
| `PR-06B` | Generic synthesize/review/policy semantic operations and unified artifact job handlers | 06A, 02D | abstention/review/replacement integration tests | EP-06A |
| `PR-06C` | Complete Dreamer policy, prompt, job, reader, and store migration to Artifact | 06B | Dreamer eval and zero legacy writes | EP-06B |
| `PR-06D` | Complete goal schema/policy/API/store migration to Artifact | 06B | adoption/change/abandonment eval | EP-06C |
| `PR-06E` | Complete storyline/worldline schema/policy/API/store migration to Artifact | 06B, 05D | temporal narrative/evidence eval | EP-06C |
| `PR-06F` | Identity/value/principle/tension/SOUL-section semantic artifact migration | 06B-06E | evidence/review/conflict semantic tests | EP-06D |
| `PR-06G` | Rebuildable SOUL projection and `SOUL.md` renderer plus direct-edit SourceEvent path | 06F | delete/rebuild and provider-outage tests | EP-06D |

#### Stage 7 — Promotion and hot context

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-07A` | ContextViewRevision persistence and `assemble_context.v1` LLM ranking/tier operation | 01C, 03G, 05D, 02D | archive-unchanged semantic ranking tests | EP-07A |
| `PR-07B` | Deterministic order-preserving token packer and minimum compressed-stub renderer | 07A | packer properties and stub invariants | EP-07B |
| `PR-07C` | Context APIs, hot-context projection, diff/unpack, triggers, and downstream task/token evaluation | 07B, 05F | every stub unpacks; task threshold passes | EP-07B |

#### Stage 8 — Unified retrieval

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-08A` | Retrieval domain models and `plan_retrieval.v1` operation with effort/capability budgets | 04D, 05F, 07C, 02D | all capability combinations plan correctly | EP-08A |
| `PR-08B` | Unified semantic, lexical/source, archive, claim, artifact, and hot-context collectors | 08A | neutral-collector and watermark tests | EP-08A |
| `PR-08C` | Resolver-first association/inverse/causal expansion and evidence-graph assembly | 08B, 04D, 05F | ambiguity and causal benchmark | EP-08B |
| `PR-08D` | Authorized source hydration adapters, cache, failure behavior, and injection isolation | 08C | tenant/auth/unavailable-source tests | EP-08C |
| `PR-08E` | `judge_evidence.v1`, bounded iterative retrieval orchestration, and sufficiency/blocked states | 08D | iteration/budget/limitation tests | EP-08C |
| `PR-08F` | `synthesize_answer.v1`, abstention, mechanical citation verifier, and retrieval audit response | 08E | answer-support/citation/adversarial eval | EP-08D |
| `PR-08G` | `/v1/retrieve`, SDK method, and complete public search/recall/causal/trace wrapper convergence | 08F | all public paths reach one orchestrator | EP-08D |

#### Stage 9 — Projections, worker, configuration, and boundaries

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-09A` | Projection protocol, registry, runner, namespace versioning, watermark, and swap verification | 01D, 01J, 08G | generic replay/crash/watermark tests | EP-09A |
| `PR-09B` | Current-state and search projection migration to the registry with rebuild drills | 09A, 04D | SQL-only delete/rebuild parity | EP-09A |
| `PR-09C` | Graph projection migration to the registry with rebuild and query parity | 09A, 05F | SQL-only graph rebuild parity | EP-09A |
| `PR-09D` | Artifact-current, hot-context, and SOUL projection migration with independent rebuild drills | 09A, 06G, 07C | all three projections delete/rebuild | EP-09A |
| `PR-09E` | Complete legacy producer/trigger conversion to the mandatory target job catalog | 01J, 03G, 04D, 05E, 06G, 07C, 08G | expected-obligation comparison has zero gaps | EP-09B |
| `PR-09F` | Legacy queue import/drain, consumer shutdown, and one worker executable cutover | 09E | no active legacy producer/consumer | EP-09B |
| `PR-09G` | Typed configuration groups/presets and environment-variable migration | 09F | no invariant-changing configuration test | EP-09C |
| `PR-09H` | Integration boundary enforcement, small public SDK surface, and internal caller cleanup | 03G, 08G, 09G | forbidden-import/caller inventory closure | EP-09C |

#### Stage 10 — Migration, cutover, and deletion

| PR phase | One complete PR outcome | Depends on | Primary proof | Workstream |
|---|---|---|---|---|
| `PR-10A` | Production importer framework, checkpoints, reports, SourceEvent/bead transforms, resume, and ID maps | 00D, 09H | source/bead fixture idempotency | EP-10A |
| `PR-10B` | Claim/Revision/association transforms, ambiguity preservation, direction quarantine/re-review | 10A, 04D, 05F | claims/associations migration matrix | EP-10A |
| `PR-10C` | Artifact/SOUL/context/outstanding-job transforms and authority classification | 10A, 06G, 07C, 09F | artifact/context/job reconciliation | EP-10A |
| `PR-10D` | Shadow-read and semantic differential-report tooling across all target capabilities | 10A-10C | classified target/legacy delta report | EP-10B |
| `PR-10E` | Interrupted import, backup/restore, projection rebuild, provider outage, cutover, and rollback rehearsal automation | 10D, 09A-09D | two complete rehearsal reports | EP-10B |
| `PR-10F` | Tenant-scoped cutover/rollback scripts, routing transaction, safety checks, and operator runbooks | 10E | disposable-environment cutover/rollback | EP-10C |
| `PR-10G` | Production canary release manifest, routing change, automated proof sequence, and release report | 10F and all gates | canary passes full monitoring window | EP-10C |
| `PR-10H` | Delete canonical JSONL/index readers/writers and obsolete file/SQLite authority backends | 10G + rollback window | runtime file-authority access count zero | EP-10D |
| `PR-10I` | Delete old write pipeline, turn authorship branches, and obsolete capture compatibility | 10H | every writer uses SourceEvent path | EP-10D |
| `PR-10J` | Delete duplicate claim resolver/store paths and legacy association engines/stores | 10I | one resolver/association path checks | EP-10D |
| `PR-10K` | Delete feature-specific Dreamer/goal/storyline/SOUL and promotion authority engines | 10J | only Artifact/context systems remain | EP-10D |
| `PR-10L` | Delete separate recall/search/causal answer pipelines and expired retrieval wrappers | 10K | all answer paths use RetrievalPipeline | EP-10D |
| `PR-10M` | Delete legacy queues/workers, invariant-changing flags, expired config aliases, and residual compatibility modules | 10L | zero legacy queue/config/caller checks | EP-10D |
| `PR-10N` | Empty architecture allowlists, update canonical docs/status, and publish final Definition-of-Done report | 10M | full suite, zero counts, and final trace audit | EP-10D |

### 6.6 Atomic phase acceptance contract

Every row above must satisfy all of these before its one complete push:

- the listed outcome works end to end within its declared boundary;
- no method, migration, test, metric, or documented behavior required by that
  row is left as a stub or deferred placeholder;
- all declared dependencies are already merged to main;
- targeted unit, architecture, contract, property, semantic, security,
  migration, or rebuild tests pass as applicable;
- the PR describes the old authority still present and the exact deletion phase;
- any schema or configuration change is deployable in dependency order;
- rollback or safe disablement is implemented and tested;
- documentation and public contract examples match the shipped behavior;
- the branch contains only that phase's scope and unrelated user work is absent;
- the implementation has been reviewed locally before the complete push.

Passing only a subset changes the phase state to `blocked` or `in progress`; it
does not justify opening a partial PR.

---

## 7. Program Stage 0 — Contract, Evaluation, and Inventory

Stage 0 prevents the implementation from optimizing for syntactic correctness
while preserving the current semantic quality problem.

From this section through Stage 10, `EP-*` headings are detailed architectural
workstream specifications. They are intentionally broader than PRs. Delivery
must use the mapped atomic `PR-*` phases in Section 6.5; an `EP-*` workstream is
never pushed or opened as a single PR unless it maps to exactly one listed
phase.

### EP-00A — Architecture Contract and Boundary Tests

**Objective:** Turn the approved invariants into executable architecture rules
before new runtime code lands.

**Dependencies:** none.

**Add:**

```text
docs/architecture/observation-ledger-contract.md
tests/architecture/test_observation_ledger_boundaries.py
tests/architecture/test_no_new_jsonl_authority.py
tests/architecture/test_no_semantic_fallback_contract.py
tests/architecture/approved_legacy_authority_paths.txt
```

**Implementation:**

1. Copy the invariant IDs and concise enforcement rules from the PRD into the
   architecture contract; link rather than duplicate full product prose.
2. Generate an explicit allowlist of current legacy modules that read or write
   JSONL, `.beads/index.json`, feature stores, or old queues.
3. Fail CI when a new path is added to that allowlist automatically or when a
   non-allowlisted module introduces those authorities.
4. Detect imports that violate the target dependency direction.
5. Scan semantic operation contracts for `fallback`, `heuristic_default`,
   `auto_accept`, or equivalent fields; permit documented legacy occurrences
   only through an expiring allowlist.
6. Add a machine-readable deletion owner and target PR phase beside each
   legacy exception.
7. Make checks path- and symbol-aware enough that comments and migration
   fixtures do not trigger false positives.

**Tests:** self-tests with temporary fixture packages proving new violations
fail and allowlisted legacy paths pass.

**Exit gate:** CI prevents architecture debt from increasing; every exception
names its EP-10 deletion target.

**Rollback:** remove only the failing rule after documenting a false-positive
case; do not expand the exception list without review.

### EP-00B — Versioned Semantic Evaluation Corpus and Baseline

**Objective:** Establish measurable quality thresholds for observation,
assertion, association, artifact, promotion, and retrieval semantics.

**Dependencies:** EP-00A.

**Add:**

```text
evals/observation_ledger/v1/manifest.yaml
evals/observation_ledger/v1/cases/*.yaml
evals/observation_ledger/v1/rubrics/*.yaml
evals/observation_ledger/v1/adjudications/*.yaml
core_memory/evaluation/observation_ledger_runner.py
core_memory/evaluation/independent_judge.py
scripts/run_observation_ledger_evals.py
docs/quality/observation-ledger-baseline.md
```

**Corpus requirements:**

- at least 100 observation events spanning conversation, tool use, file edits,
  failures, retries, external-source use, and mixed user/agent action;
- at least 25 deliberately low-semantic-content cases such as greetings,
  acknowledgements, duplicated messages, and operational noise;
- at least 50 claim/revision chains covering reaffirm, supersede, retract,
  contest, resolve, ambiguous terminals, broken links, and cycles;
- at least 75 association candidate sets containing true links, no-link cases,
  direction traps, chronology/causality traps, and unknown valid time;
- at least 30 artifact cases across Dreamer, goals, storylines, lessons,
  principles, identity, values, tensions, and SOUL;
- at least 30 promotion cases with downstream task evaluation, token pressure,
  pinned requirements, and later unpack;
- at least 75 retrieval questions requiring semantic search, current-state
  resolution, causal expansion, source hydration, ambiguity, and abstention;
- source materials licensed or generated for durable repository use;
- tenant-boundary and prompt-injection adversarial cases.

**Ground truth policy:**

1. Two independent human adjudications for high-authority cases.
2. Disagreement remains encoded rather than majority-flattened.
3. Rubrics distinguish evidence faithfulness, schema validity, appropriate
   abstention, and usefulness.
4. An LLM judge may score scale dimensions only with versioned prompts and a
   calibrated human agreement report.
5. The model producing an output cannot be its only evaluator.

**Baseline report:** record current-master scores, unsupported-output examples,
latency percentiles, cost, retry rate, fallback incidence, and missing evidence
provenance. Preserve raw result IDs and exact model/prompt versions.

**Threshold setting:** invariant violations have a zero-tolerance gate. Numeric
quality thresholds are proposed from the baseline, reviewed, and committed in
`manifest.yaml` as the complete outcome of `PR-00T`, before Stage 3 cutover.

**Exit gate:** the corpus is versioned, reproducible, independently judged,
and capable of failing known current-quality defects.

**Rollback:** corpus cases are append-only within a version. Corrections create
an adjudication revision; a new major corpus version is required for changed
meaning.

### EP-00C — Legacy State and Authority Inventory

**Objective:** Produce the complete input manifest for migration and deletion.

**Dependencies:** EP-00A.

**Add:**

```text
core_memory/migration/inventory.py
core_memory/migration/classification.py
scripts/inventory_legacy_memory.py
docs/migration/legacy-authority-classification.md
tests/migration/test_inventory_read_only.py
```

**Inventory:**

- every JSONL session/bead file and mutable index;
- SQLite projection-cache databases;
- claims, association candidates, coverage, Dreamer, SOUL, goals, storylines,
  and promotion files/tables;
- old job/queue/candidate stores;
- vector and graph namespaces;
- adapter-specific state and external source handles;
- direct read and write call sites;
- environment variables that select authority or semantic fallbacks.

**Manifest fields:** tenant/workspace, path or table, record kind, count, byte
size, min/max time, stable content hash, parse failures, duplicate IDs, source
recoverability, provenance class, current reader symbols, current writer
symbols, proposed importer, and deletion package.

**Provenance classes:**

```text
observed_source
llm_authored_with_receipt
llm_authored_without_receipt
deterministic_derived
human_authored
projection_only
unknown
```

**Safety:** the inventory command is read-only, accepts explicit roots, refuses
broad home/root targets, emits no semantic upgrades, and writes its report to a
separate requested destination.

**Exit gate:** every live authority path and state class has a count/hash,
classification rule, importer disposition, and deletion owner.

**Rollback:** not applicable; the tool is read-only. Incorrect classification
is corrected through a versioned mapping file.

### Stage 0 completion gate

- [ ] Invariant boundary tests run in required CI.
- [ ] Evaluation corpus catches known unsupported semantics.
- [ ] Current quality and latency baseline is published.
- [ ] Numeric semantic thresholds are reviewed and versioned.
- [ ] Legacy authority inventory reconciles with known runtime paths.
- [ ] No observation, optional-assertion, revision, evidence, promotion, or
      retrieval definition remains unresolved.

---

## 8. Program Stage 1 — Domain Kernel, SQL Ledger, and One Job Protocol

### EP-01A — Canonical Domain Kernel and Vocabulary Registry

**Objective:** Create immutable, persistence-neutral domain types and freeze v1
vocabulary mechanics.

**Dependencies:** EP-00A; vocabulary choices informed by EP-00B.

**Add:** the `core_memory/domain/` package listed in Section 3.1 and focused
unit/property tests under `tests/domain/`.

**Implementation:**

1. Define opaque ID aliases and constructors with no embedded object meaning.
2. Define `TenantScope`, `LedgerEnvelope`, `SourceEvent`, `EvidenceRef`,
   `ObservationBead`, `ClaimAssertion`, `AssociationAssertion`, `Revision`,
   `EvidenceSet`, `Artifact`, `ArtifactReview`, `ContextViewRevision`,
   `SemanticTaskReceipt`, and `Job` as frozen domain values.
3. Model unknown semantic time as `None`, never as recorded time or epoch.
4. Represent valid intervals independently from recorded/system time.
5. Define canonical association predicates and an explicit inverse-display
   mapping; inverse labels are not storable predicates.
6. Define scope-key constructors for claims, associations, artifacts, and bead
   versions. Scope keys are structural identifiers, not semantic similarity.
7. Validate evidence coordinates by source payload kind: message IDs, character
   spans, JSON pointers, file/blob ranges, tool call/result IDs, or external
   handles.
8. Keep assertions optional in `AnnotationBundle`; require exactly one bead.
9. Reject unknown vocabulary values unless the operation explicitly uses a
   versioned extension registry.
10. Provide canonical serialization used by both SQL adapters and semantic
    schema generation.

**Tests:** round-trip serialization, frozen behavior, evidence coordinate
matrix, temporal interval matrix, inverse mapping, optional assertion bundle,
cross-kind revision rejection, canonical JSON, and invalid vocabulary cases.

**Exit gate:** domain tests have no persistence/runtime imports; v1 schema
snapshots and vocabulary versions are reviewed.

**Rollback:** no production callers yet. Schema changes amend the domain
version before any ledger migration is released.

### EP-01B — Versioned SQL Schema and Migration Runner

**Objective:** Define one logical ledger schema for SQLite and PostgreSQL with
append-only and tenancy constraints.

**Dependencies:** EP-01A, EP-00C inventory shape.

**Add:**

```text
core_memory/ledger/protocol.py
core_memory/ledger/transactions.py
core_memory/ledger/errors.py
core_memory/ledger/migrations.py
core_memory/ledger/schema/sqlite/0001_ledger.sql
core_memory/ledger/schema/postgres/0001_ledger.sql
core_memory/ledger/schema/sqlite/0002_jobs.sql
core_memory/ledger/schema/postgres/0002_jobs.sql
core_memory/ledger/schema/sqlite/0003_projections.sql
core_memory/ledger/schema/postgres/0003_projections.sql
tests/ledger/test_schema_manifest.py
```

**Migration runner:**

- records dialect, version, checksum, applied time, and application build;
- refuses a changed checksum for an applied migration;
- obtains a database-level migration lock;
- supports inspect, plan, apply, and verify modes;
- never auto-downgrades canonical data;
- emits a machine-readable report;
- separates schema migration from legacy data import.

**Schema checks:** foreign keys enabled in SQLite, UTC timestamp normalization,
check constraints mirrored where possible, partial unique indexes tested, all
tenant-bearing foreign keys include tenant identity, and canonical immutability
triggers installed.

**Exit gate:** fresh schema creation and incremental migration pass on both
dialects; schema manifest comparison documents unavoidable dialect differences.

**Rollback:** forward-fix before data cutover. During pre-production only, a
database snapshot may restore the preceding schema. No destructive down
migration ships as routine behavior.

### EP-01C — SQLite Ledger Adapter

**Objective:** Implement the canonical local ledger with transactional append,
idempotency, and online backup.

**Dependencies:** EP-01B.

**Add:**

```text
core_memory/ledger/sqlite.py
core_memory/ledger/queries.py
core_memory/ledger/backup.py
tests/ledger/contract/
tests/ledger/test_sqlite_concurrency.py
tests/ledger/test_sqlite_backup_restore.py
```

**Implementation:**

- enable WAL, foreign keys, busy timeout, explicit transactions, and durable
  synchronization appropriate to canonical memory;
- use one connection per unit of work and never share cursors across threads;
- allocate tenant ledger positions under `BEGIN IMMEDIATE`;
- implement capture, annotation commit, revisions, artifacts, context, receipts,
  and ledger scans through intention-revealing commands;
- return the original receipt on an idempotency replay;
- distinguish uniqueness conflict, optimistic precondition failure, transient
  lock, corrupt database, and contract violation;
- implement SQLite online backup with schema version, last ledger position,
  file hash, and verification reopen;
- expose transaction test hooks only in test support, not production API.

**Contract tests:** event+job atomicity, annotation bundle atomicity, rollback at
each statement boundary, append immutability, tenant isolation, replay,
concurrent duplicate capture, concurrent revisions, scan ordering, backup,
restore, and projection replay seed.

**Exit gate:** the shared ledger contract passes with injected crashes and
concurrency; no canonical write uses `INSERT OR REPLACE`.

**Rollback:** adapter remains unused by default until Stage 3. Remove its
registration without altering legacy state.

### EP-01D — PostgreSQL Ledger Adapter, RLS, and Recovery Contract

**Objective:** Implement hosted canonical persistence with database-enforced
tenant isolation and safe concurrent appends.

**Dependencies:** EP-01B.

**Add:**

```text
core_memory/ledger/postgres.py
core_memory/ledger/postgres_session.py
tests/ledger/test_postgres_contract.py
tests/ledger/test_postgres_rls.py
tests/ledger/test_postgres_leasing.py
docs/runbooks/ledger-postgres-backup-restore.md
```

**Implementation:**

- use psycopg v3 pools only at the application composition root;
- set tenant/workspace context transaction-locally and fail closed if absent;
- install RLS policies on every tenant-bearing table, including projections and
  jobs;
- use tenant-qualified foreign keys and unique constraints;
- allocate positions atomically without global tenant contention;
- use server-side UTC and return stored values to callers;
- set statement and lock timeouts by operation class;
- prevent application roles from mutating canonical rows;
- document PITR/snapshot expectations and test logical backup/restore in CI or
  a disposable integration environment.

**Security tests:** missing tenant context, forged tenant predicate, cross-tenant
EvidenceRef, cross-tenant revision, background-worker context, admin/read-only
roles, and connection-pool tenant leakage.

**Exit gate:** the same contract suite as SQLite passes; RLS blocks every tested
cross-tenant path independently of application filters.

**Rollback:** unregister hosted adapter before canonical cutover; schema remains
empty or isolated until explicitly removed by a reviewed migration.

### EP-01E — Unified Jobs Table, Worker Registry, and Reconciler

**Objective:** Create the only target protocol for all deferred work.

**Dependencies:** EP-01C and EP-01D.

**Add:** the `core_memory/jobs/` package in Section 3.1 plus shared job contract
tests.

**Job states:**

```text
pending -> leased -> succeeded
                 -> pending (retry)
                 -> failed
                 -> quarantined
```

Expired `leased` jobs become eligible for recovery; they do not create a
second canonical output because handlers and commits are idempotent.

**Required fields:** tenant/workspace, kind/version, subject kind/ID, dedupe key,
priority, eligibility time, attempt count/max, lease owner/token/expiry,
dependency state, input reference, operation ID, last error class, terminal
reason, created/updated operational timestamps.

**Implementation:**

1. Define a versioned handler registry; unknown job kinds quarantine rather
   than succeed.
2. PostgreSQL leases with ordered `FOR UPDATE SKIP LOCKED`; SQLite leases in a
   short immediate transaction.
3. Heartbeats extend leases only for the matching opaque lease token.
4. Retry policy is typed by error class with capped exponential backoff and
   jitter; semantic retry policy comes from the semantic runtime, not handler
   guesswork.
5. Static dependencies live in `job_dependencies`; dynamic obligations are
   created transactionally by the canonical commit that discovers them.
6. Reconciliation scans canonical ledger rows after a durable checkpoint and
   recreates absent obligations by deterministic dedupe key.
7. Reconciliation never authors semantics or infers that a relationship should
   exist; it only restores required work.
8. Local in-process mode starts the same `Worker` with a SQLite `JobStore`.
9. Expose queue depth, oldest eligible age, lease expiry, attempt, terminal,
   and obligation-gap metrics by tenant and kind.

**Fault tests:** crash before handler, crash after semantic call/before commit,
crash after canonical commit/before job success, expired lease, duplicate
workers, dependency failure, retry exhaustion, unknown handler, and reconciler
replay.

**Exit gate:** one jobs-table contract passes on both databases; reconciliation
reduces injected mandatory-obligation gaps to zero without duplicate semantics.

**Rollback:** new worker registration can be disabled before target callers are
redirected. Do not translate target jobs into legacy queue semantics.

### Stage 1 completion gate

- [ ] Shared ledger and job contracts pass on SQLite and PostgreSQL.
- [ ] Application roles cannot update/delete canonical rows.
- [ ] Tenant position, idempotency, and concurrency tests pass.
- [ ] Mandatory append+job transactions survive injected faults.
- [ ] Backup/restore and schema checksum verification pass.
- [ ] Jobs can be leased by multiple replicas without duplicate canonical
      output.

---

## 9. Program Stage 2 — One Semantic Operation Runtime

### EP-02A — Versioned Semantic Contracts and Operation Registry

**Objective:** Replace feature-specific semantic request/result shapes with one
typed operation envelope.

**Dependencies:** EP-01A, EP-01E.

**Add:**

```text
core_memory/semantic/contracts.py
core_memory/semantic/routing.py
core_memory/semantic/validation.py
core_memory/semantic/receipts.py
core_memory/semantic/operations/registry.py
tests/semantic/test_contracts.py
tests/semantic/test_operation_registry.py
```

**Operation kinds v1:**

```text
annotate_event
revise_assertion
review_associations
synthesize_artifact
review_artifact
assemble_context
plan_retrieval
judge_evidence
synthesize_answer
verify_output
```

Each operation version defines input schema, output schema, allowed evidence
kinds, vocabulary version, routing policy, verification policy, retry classes,
and redaction policy.

**Hard rules:**

- request objects have no fallback output or default semantic decision;
- omitted semantic decision is invalid unless the schema explicitly models
  abstention/no-link/no-assertion;
- validation errors preserve JSON paths and rejected values for correction;
- provider text is never parsed with permissive regex heuristics into canonical
  semantics;
- unknown output fields fail closed unless the operation schema explicitly
  allows an extension map;
- operation version and schema hash are immutable in receipts.

**Exit gate:** every target semantic capability can be represented by one
envelope; contract tests prove no operation can request deterministic fallback.

**Rollback:** target-only types can be removed before any caller redirects.

### EP-02B — Multi-Step Runtime, Provider Routing, and Durable Attempts

**Objective:** Execute all semantic work through one runtime with retries and
truthful failure behavior.

**Dependencies:** EP-02A.

**Add:**

```text
core_memory/semantic/runtime.py
core_memory/semantic/providers/protocol.py
core_memory/semantic/providers/inline.py
core_memory/semantic/providers/delegated.py
core_memory/semantic/providers/pydantic_ai.py
tests/semantic/test_runtime_retries.py
tests/semantic/test_delegated_routing.py
```

**Execution sequence:**

1. authorize task and evidence scope;
2. resolve operation version and model route;
3. minimize and serialize evidence;
4. execute the model's multi-step operation;
5. validate output shape, vocabulary, IDs, evidence spans, and temporal fields;
6. when correctable, return structured validation feedback to the LLM;
7. optionally route a stronger model after policy-defined failures;
8. run independent semantic verification where policy requires it;
9. return validated output or typed failure;
10. append receipts/attempts even when no canonical semantic output is produced.

**Meaning-bearing conditional rule:** branching such as “does this event contain
a claim?”, “are these observations related?”, or “is evidence sufficient?” is
inside the operation prompt/output. Code may branch only on explicit returned
decisions and contract validity.

**Retry classes:** provider unavailable, timeout, rate limit, truncated output,
schema invalid, evidence invalid, verifier rejection, authorization failure,
policy refusal, and non-retryable contract error. Only policy-declared classes
retry. Retries retain one operation ID and append attempts.

**Routing:** tenant/workspace/model credentials are allowlisted metadata. The
runtime never forwards arbitrary request metadata. Provider adapters return
normalized usage, latency, route, and error classes.

**Exit gate:** provider outage and invalid output create no semantics; every
attempt is receipted; inline and delegated routes pass the same operation
contract and tenant-routing canary.

**Rollback:** route no production jobs to the new registry until EP-03B.

### EP-02C — Independent Verification and No-Fallback Closure

**Objective:** Prove the new semantic runtime cannot manufacture availability
through deterministic output.

**Dependencies:** EP-02B, EP-00B.

**Add:**

```text
core_memory/semantic/operations/verify_output.py
tests/semantic/test_no_fallback_paths.py
tests/semantic/test_live_model_smoke.py
tests/adversarial/test_semantic_authority.py
```

**Implementation:**

- define verification outcomes `accept`, `reject_retryable`, `reject_terminal`,
  and `needs_human_review`;
- verification output is itself LLM-authored when it judges meaning; code only
  validates/verifies referenced evidence mechanically;
- persist producer and verifier receipts separately;
- forbid the same model response from counting as independent verification;
- add fault injection at every runtime stage;
- add dynamic tests that replace every provider with failure/invalid-output
  stubs and assert zero bead/assertion/association/artifact rows;
- add architecture scans for old fallback symbols entering target modules;
- run the Stage 0 observation/adversarial subset against a real configured
  model and publish result artifacts.

**Exit gate:** no target operation has a deterministic semantic fallback; live
annotation smoke meets initial faithfulness/abstention gates.

**Rollback:** disable target semantic job handlers; pending jobs remain durable
for replay after correction.

### Stage 2 completion gate

- [ ] Common request/result/receipt contract covers every operation kind.
- [ ] Every provider attempt is auditable.
- [ ] Invalid output receives structured correction or remains failed/pending.
- [ ] Provider outage writes zero semantic content.
- [ ] Inline and delegated authorship converge on the same validated result
      contract.
- [ ] Live-model smoke and independent verification pass.

---

## 10. Program Stage 3 — Canonical Observation Write Pipeline

### EP-03A — SourceEvent Capture Service and Public Receipt

**Objective:** Make observed user-agent activity the sole entry point for new
canonical memory.

**Dependencies:** EP-01E, EP-02A.

**Add:**

```text
core_memory/api/service.py
core_memory/api/receipts.py
core_memory/api/errors.py
core_memory/domain/events.py
tests/api/test_observe_event.py
tests/integration/test_capture_atomicity.py
```

**Public contract:**

```text
POST /v1/events
GET  /v1/events/{event_id}
GET  /v1/operations/{operation_id}
```

`POST /v1/events` accepts authenticated tenant/workspace, source kind, source
identity, idempotency key, participants, occurred-at if observed, immutable
payload, and authorization-scoped hydration handles. It does not accept a
caller-authored bead, label, claim, association, goal, or semantic truth state.

**Transaction:** normalize mechanical fields -> authorize -> append SourceEvent
-> append `annotate_event` job -> commit -> return capture receipt.

**Receipt fields:** event ID, operation ID, annotation job ID, state `captured`,
recorded time, ledger position, idempotent replay indicator, and polling link.
It never says “memory written” before annotation commits.

**Exit gate:** every accepted event has exactly one mandatory annotation
obligation; duplicate idempotency keys return the original receipt; rejected
events write nothing.

**Rollback:** remove endpoint routing before any legacy adapter redirects; SQL
events may remain isolated test data.

### EP-03B — Annotation Operation, Bundle Validation, and Atomic Commit

**Objective:** Produce exactly one grounded observation bead lineage per event,
with optional assertions.

**Dependencies:** EP-03A, EP-02C.

**Add:**

```text
core_memory/semantic/operations/annotate_event.py
core_memory/jobs/handlers/annotate_event.py
core_memory/api/annotation.py
tests/semantic/test_annotate_event.py
tests/integration/test_annotation_commit.py
tests/adversarial/test_observation_grounding.py
```

**Annotation output:**

- one bead label selected from the frozen observation vocabulary;
- concise LLM-authored title and summary;
- evidence references covering every substantive clause;
- optional orthogonal facets;
- zero or more claim assertions;
- zero or more association review seeds/candidates only when the operation
  explicitly authors them;
- explicit abstentions and limitations;
- no derived companion bead.

**Validation stages:**

1. schema and vocabulary;
2. event and tenant identity;
3. EvidenceRef existence and coordinate range;
4. clause-to-evidence coverage structure;
5. one-bead requirement and source-event uniqueness;
6. assertion shape, subject/object, and temporal fields;
7. verifier decision when required;
8. optimistic check that the event has no committed bead lineage;
9. append transaction.

Mechanical clause coverage validation does not judge entailment. Semantic
faithfulness is evaluated by the verifier and benchmark.

**Partial optional rows:** if the bead is valid but an optional child fails
child-specific validation, commit the bead, accepted children, full receipt,
and a `repair_annotation_children` job containing rejected child indexes and
validation feedback. The operation state is `completed_with_repairs`. If the
bead is invalid, commit only failure receipts/job state and no semantics.

**Dependent jobs:** transactionally create projection, association coverage,
artifact-policy evaluation, and context-refresh obligations according to the
static dependency registry. These jobs do not imply that semantic outputs must
exist.

**Adversarial proofs:** greeting creates a faithful low-content bead with zero
assertions; repeated keyword cannot create a goal; tool failure remains a tool
failure observation; unsupported summary is rejected; duplicate delivery does
not duplicate bead lineage.

**Exit gate:** one event/one bead lineage, zero-assertion success, grounded
EvidenceRefs, no fallback bead, truthful receipts, and retry idempotency all
pass on SQLite and PostgreSQL.

**Rollback:** disable the annotation handler and leave events/jobs pending.
Never invoke an old deterministic bead writer for target events.

### EP-03C — Adapter Convergence and Legacy Write Compatibility

**Objective:** Route every current capture integration through SourceEvent and
the same annotation pipeline.

**Dependencies:** EP-03B.

**Primary current call surfaces to inspect and redirect:**

```text
core_memory/runtime/engine.py
core_memory/runtime/turn/turn_flow.py
core_memory/runtime/queue/worker.py
core_memory/runtime/passes/
core_memory/write_pipeline/
core_memory/hosted_bridge/
core_memory/integrations/
HTTP and CLI write endpoints
```

**Implementation:**

1. Define small adapter inputs for message turns, tool calls/results, file
   changes, external documents, and host-authored events.
2. Translate only observed payload and mechanical source identity.
3. Remove adapter-side bead labeling, claim creation, association writing, and
   fallback summaries from the redirected path.
4. Support inline completion by waiting on the same operation/job; do not call
   a second inline writer.
5. Support delegated completion by polling/submitting the same operation ID and
   validating through `SemanticRuntime`.
6. Return capture and semantic receipts separately through legacy APIs.
7. Add deprecation telemetry for each legacy method and header.
8. Create an explicit caller inventory; every production writer must be marked
   redirected, migration-only, or scheduled for deletion.

**Compatibility rule:** old callers may receive a translated response shape,
but the adapter cannot claim success before the target receipt supports it.

**Exit gate:** production capture callers use one SourceEvent transaction and
one annotation operation; no redirected caller writes JSONL/index semantics or
invokes a fallback generator.

**Rollback:** per-adapter routing may return to the legacy pipeline only before
tenant cutover. Captured target events remain in SQL and are not mirrored to
JSONL.

### Stage 3 completion gate

- [ ] Every accepted SourceEvent atomically creates an annotation job.
- [ ] Every successful annotation produces exactly one bead lineage.
- [ ] Assertions are optional and absence is normal.
- [ ] Invalid output creates no fallback semantics.
- [ ] All current adapters use the target pipeline or are explicitly disabled.
- [ ] Capture and semantic completion are separately observable.

---

## 11. Program Stage 4 — Revision Ledger and One Current-State Resolver

### EP-04A — Revision Domain, Pure Resolver, and Property Suite

**Objective:** Make explicit revisions—not arrival order—the only way semantic
objects change current state.

**Dependencies:** EP-03B.

**Add or replace:**

```text
core_memory/domain/revisions.py
core_memory/domain/resolver.py
tests/domain/test_revision_actions.py
tests/domain/test_resolver_matrix.py
tests/property/test_revision_graphs.py
```

**Current predecessor to retire from canonical use:**

```text
core_memory/claim/resolver.py
feature-specific latest/current/supersede helpers
```

**Revision actions:**

```text
reaffirm
supersede
retract
contest
resolve_contest
replace_artifact
replace_bead_version
replace_context_view
```

Each action has an explicit compatibility matrix covering predecessor kind,
successor kind, required evidence, whether a successor is required, and valid
scope relationship. There is no generic `update` action.

**Resolver algorithm:**

1. Validate every object and Revision belongs to the requested scope and
   tenant.
2. Filter objects and revisions by recorded/known-time cutoff when supplied.
3. Filter semantic validity by valid-time instant or interval when supplied.
4. Build the directed revision graph.
5. Reject missing nodes, incompatible actions, cross-scope edges, cycles, and
   multiply-defined replacement edges as `invalid`.
6. Compute terminal objects after explicit retract/supersede/resolve actions.
7. Preserve contest edges separately from replacement edges.
8. Return `absent` when no valid terminal exists.
9. Return `retracted` when all otherwise eligible terminals are explicitly
   retracted.
10. Return `contested` when a terminal has an unresolved contest state.
11. Return `ambiguous` when multiple incompatible unlinked terminals remain.
12. Return `current` only when the revision graph uniquely supports it.

The resolver must not inspect confidence, salience, repetition, myelination,
embedding similarity, job completion order, or ledger position to break a
semantic tie.

**Property tests:** input-order independence, append-prefix stability,
as-of monotonic knowledge, cycle detection, no latest-wins behavior, contest
preservation, compatible reaffirm behavior, and deterministic serialization of
the same valid graph.

**Exit gate:** one pure resolver passes the full terminal-state and temporal
matrix; adversarial unlinked conflicting terminals resolve `ambiguous`.

**Rollback:** resolver is target-only until EP-04B redirects current-state
consumers.

### EP-04B — Claim Assertions, Revision Operation, and Current-State Projection

**Objective:** Route all claim creation and change through append-only
assertions, LLM-authored revision decisions, and the unified resolver.

**Dependencies:** EP-04A, EP-02C.

**Add:**

```text
core_memory/semantic/operations/revise_assertion.py
core_memory/jobs/handlers/revise_assertion.py
core_memory/projections/current_state.py
core_memory/api/assertions.py
tests/integration/test_claim_revision_flow.py
tests/integration/test_current_state_projection.py
```

**Current predecessor to redirect/delete later:**

```text
core_memory/persistence/store_claim_ops.py
claim-specific mutable current flags
duplicate claim resolution in recall/search paths
```

**Write behavior:**

- a new observed assertion appends as a candidate terminal in its structural
  scope;
- when prior assertions exist, `revise_assertion` receives the new assertion,
  prior terminals, evidence, and revision vocabulary;
- the LLM must choose and justify an explicit action or return abstain/needs
  review where allowed;
- missing action, incompatible predecessor, or unsupported justification is
  invalid and retries; it never defaults to reaffirm or supersede;
- accepted action appends a Revision and, if appropriate, a successor
  assertion in one transaction;
- independent contradictory assertions may deliberately remain unlinked and
  therefore ambiguous until a later evidence-bound resolution.

**Projection:** `current_object_state` stores scope key, resolution state,
terminal IDs, contest IDs, resolver version, source ledger watermark, and
computed time. It is acceleration only. Canonical APIs may recompute from the
ledger and compare projection output.

**API:**

```text
GET  /v1/assertions/{id}
GET  /v1/assertion-scopes/{scope_key}/current
GET  /v1/assertion-scopes/{scope_key}/history
POST /v1/assertion-scopes/{scope_key}/revisions
```

The POST accepts an authenticated source observation or human review event,
not an in-place state change. Human edits are captured as SourceEvents and then
appended with explicit human authority provenance.

**Exit gate:** all target claim consumers resolve through the same resolver;
projection and direct resolution agree; no current flag mutates canonical
assertions.

**Rollback:** disable revision operation handlers and direct callers back only
before target tenant cutover. Appended history remains valid and immutable.

### EP-04C — Bi-Temporal Queries, Revision Audit, and Resolver Convergence

**Objective:** Expose valid-time and known-time state consistently and remove
duplicate canonical resolution paths.

**Dependencies:** EP-04B.

**Add:**

```text
core_memory/domain/time.py
core_memory/ledger/as_of_queries.py
core_memory/api/history.py
tests/contract/test_bitemporal_queries.py
tests/architecture/test_single_resolver.py
```

**Time contract:**

- `recorded_at` is database-assigned system time and always present;
- `valid_from` and `valid_to` are LLM-authored semantic fields only when
  evidenced, otherwise unknown;
- `known_at` query filters what had been recorded by the cutoff;
- `valid_at` query filters what the semantic assertion says was true at that
  time;
- both can be supplied to answer “what did Core Memory know then about what
  was true at another time?”;
- time zones and interval endpoints are normalized mechanically without
  inventing missing time precision.

**Convergence:** inventory every import/call of old claim resolvers, latest-row
selectors, current flags, and retrieval-specific resolution. Redirect target
callers to `CurrentStateResolver`. Add an architecture test allowing the old
resolver only in the migration reader until EP-10D.

**Audit response:** include scope, query cutoffs, eligible object IDs, applied
Revision IDs, resolution state, current terminals, unresolved terminals, and
resolver/schema versions. Do not expose hidden chain-of-thought.

**Exit gate:** the valid/known-time matrix passes on both databases; every
target current-state consumer uses one resolver; cyclic/broken graphs visibly
return `invalid`.

**Rollback:** API can be hidden before production publication; resolver data is
append-only and needs no reversal.

### Stage 4 completion gate

- [ ] Assertions remain optional at observation time.
- [ ] All semantic changes append explicit Revisions.
- [ ] No arrival-order or latest-row semantic winner exists.
- [ ] Ambiguous, contested, retracted, absent, and invalid are first-class.
- [ ] Valid-time and known-time queries pass the full matrix.
- [ ] Exactly one resolver serves all target consumers.

---

## 12. Program Stage 5 — Association Coverage and Aggregate Evidence

### EP-05A — Neutral Association Candidate Collection

**Objective:** Consolidate deterministic discovery signals without allowing a
signal to become a semantic relationship.

**Dependencies:** EP-03B, EP-01E.

**Add:**

```text
core_memory/association/candidates.py
core_memory/association/models.py
core_memory/jobs/handlers/collect_association_candidates.py
tests/association/test_neutral_candidates.py
tests/architecture/test_candidates_cannot_write_edges.py
```

**Current source modules to extract from, not preserve as authorities:**

```text
core_memory/runtime/associations/coverage.py
core_memory/association/preview.py
core_memory/association/crawler_contract.py
feature-specific candidate stores
```

**Candidate signals:** semantic-vector neighbors, shared entities, shared
source/session, temporal proximity, common artifact membership, graph frontier,
unresolved claim scope, and explicit user/agent references. Signals carry
scores and provenance solely for recall and candidate budgeting.

**Candidate record:** subject bead/assertion, candidate object, neutral signal
list, source ledger watermarks, collection version, eligibility reason,
previous review state, and dedupe key. It contains no accepted predicate,
direction, valid interval, causal status, or truth confidence.

**Rules:**

- keyword or embedding score cannot author `similar_to`;
- chronological order cannot author causal direction;
- a shared source does not establish independence or support;
- collectors may omit candidates only by mechanical budgets disclosed to the
  review operation;
- candidates are operational records and may be compacted after their immutable
  review receipts remain canonical;
- repeated collection dedupes the review obligation by subject, candidate,
  collector version, and relevant source watermark.

**Exit gate:** collectors increase candidate coverage but cannot insert an
association assertion through any code path.

**Rollback:** disable collector handlers; no canonical semantics are affected.

### EP-05B — LLM Association Review, Direction, and Temporal Semantics

**Objective:** Make the LLM the only author of relationship existence,
predicate, canonical direction, qualifiers, and semantic valid time.

**Dependencies:** EP-05A, EP-02C, EP-04A.

**Add:**

```text
core_memory/semantic/operations/review_associations.py
core_memory/jobs/handlers/review_associations.py
core_memory/api/associations.py
tests/semantic/test_association_review.py
tests/adversarial/test_association_direction.py
```

**Operation input:** focal evidence, bounded candidate evidence, prior accepted
associations and Revisions in scope, neutral candidate signals, canonical
predicate registry with definitions, inverse-display mapping, and explicit
no-link option.

**Operation output per candidate:**

```text
accept | reject | defer
canonical predicate when accepted
canonical source and target IDs
evidence refs for relation and direction
valid_from/valid_to when evidenced
qualifiers and limitations
confidence as model self-report only, never truth state
revision action when a prior edge is affected
```

**Persistence:** every candidate decision appends an immutable
`association_review_decisions` row linked to its semantic receipt. Accepted
associations additionally append once in canonical forward direction.
Rejected/deferred decisions remain auditable for coverage and precision but do
not create negative truth edges unless a separate evidence-bound assertion
explicitly models one.

**Inverse traversal:** deterministic code uses the registry's inverse display
mapping at read time. It never writes a second inverse association row.

**No-link:** a valid no-link review is successful semantic work. It contributes
to review coverage and precision without increasing graph density.

**Re-review:** a new evidence watermark or operation/predicate version may
schedule review. Changed accepted meaning appends a new association plus
Revision; it never mutates the prior edge.

**Exit gate:** association benchmark passes direction, evidence, unknown-time,
and no-link gates; no deterministic path can write an edge.

**Rollback:** pause review handlers; candidate and review obligations remain
pending. Never promote candidates directly.

### EP-05C — Coverage Projection, EvidenceSet Aggregation, and Graph Rebuild

**Objective:** Turn reviewed associations into auditable coverage and aggregate
evidence without conflating volume with truth.

**Dependencies:** EP-05B, EP-04C.

**Add:**

```text
core_memory/domain/evidence_sets.py
core_memory/association/coverage.py
core_memory/association/evidence.py
core_memory/projections/graph.py
core_memory/jobs/handlers/rebuild_graph.py
tests/association/test_coverage_accounting.py
tests/association/test_evidence_independence.py
tests/integration/test_graph_rebuild.py
```

**Coverage dimensions:** eligible candidates, collected candidates, reviewed
candidates, accepted/rejected/deferred, stale reviews, failed jobs, oldest
unreviewed age, source ledger watermark, reviewer operation version, and
predicate registry version. Report operational completeness and semantic
quality separately.

**EvidenceSet membership:** each member records assertion/association/bead,
stance `supports|contradicts|contextualizes`, source root, independence group,
valid interval, known time, and inclusion rationale authored by the relevant
LLM operation.

**Independence:** deterministic grouping may establish obvious shared-root
identity from source lineage. It cannot declare semantically independent
corroboration merely because records have different IDs. Uncertain independence
stays unknown and is surfaced to evidence judgment.

**Aggregate evidence is not a scalar truth score.** APIs expose support,
contradiction, source-root distribution, temporal distribution, contest state,
and unresolved independence. Repetition/salience may rank navigation but cannot
change resolution state.

**Graph projection:** stores canonical edge ID, endpoints, predicate, inverse
display data, resolved state, valid interval, evidence-set ID, and ledger
watermark. Delete/rebuild scans SQL and produces byte- or semantic-equivalent
normalized output.

**Exit gate:** coverage gaps are visible, evidence provenance/contradiction is
preserved, and graph projection rebuild succeeds from SQL alone.

**Rollback:** delete projection and rebuild; canonical associations and
EvidenceSets remain unchanged.

### Stage 5 completion gate

- [ ] Candidate collection never authors relation meaning.
- [ ] Every accepted edge has an LLM receipt and evidence.
- [ ] Canonical direction is stored once; inverse traversal is computed.
- [ ] Unknown semantic time remains unknown.
- [ ] Rejected/no-link decisions count toward coverage without becoming edges.
- [ ] Aggregate evidence preserves support, contradiction, source roots, and
      independence uncertainty.
- [ ] Graph projection deletes and rebuilds from SQL.

---

## 13. Program Stage 6 — One Append-Only Artifact System

### EP-06A — Artifact Kernel, Lifecycle, Evidence, and Review

**Objective:** Provide one generic evidence/revision lifecycle for all derived
memory products while retaining typed content schemas.

**Dependencies:** EP-03B, EP-04C, EP-05C.

**Add:**

```text
core_memory/domain/artifacts.py
core_memory/semantic/operations/synthesize_artifact.py
core_memory/semantic/operations/review_artifact.py
core_memory/jobs/handlers/synthesize_artifact.py
core_memory/jobs/handlers/review_artifact.py
core_memory/projections/artifacts.py
core_memory/api/artifacts.py
tests/artifacts/test_artifact_lifecycle.py
```

**Artifact kinds v1:**

```text
dream
goal
storyline
worldline
lesson
principle
identity
value
tension
soul_section
```

New kinds register a typed content schema, synthesis/review policy, evidence
requirements, and render adapter. They do not create a new persistence or
revision system.

**Lifecycle:**

```text
proposed -> accepted
         -> rejected
         -> needs_review
accepted -> replaced by new artifact + Revision
```

These states describe review lifecycle, not mutation. Every artifact row is
immutable. Replacement appends a successor and `replace_artifact` Revision.

**Artifact fields:** kind/version, lineage ID, typed content JSON, title/summary
where applicable, EvidenceSet, limitations, synthesis receipt, verification
receipt, required review policy, valid time if applicable, and rendering hints
that do not contain hidden semantic authority. Review decisions append as
immutable `artifact_reviews` rows. Accepted/rejected/needs-review status is a
resolver/projection result and never an updated Artifact field.

**Policy engine:** deterministic code may decide mechanically that a configured
artifact policy is due (for example, N new ledger positions since last
evaluation). The LLM decides whether evidence supports an artifact and may
abstain. Due work never guarantees content.

**Human edits:** enter as SourceEvents with human authority. The resulting
accepted replacement and Revision preserve both the prior artifact and human
provenance.

**Exit gate:** generic create/review/revise/read/history APIs work for at least
two different artifact kinds and share identical persistence lifecycle.

**Rollback:** pause artifact jobs. Existing accepted target artifacts remain
readable and immutable.

### EP-06B — Dreamer Migration to Artifact Policies

**Objective:** Retain Dreamer capability while deleting it as an independent
semantic and storage engine.

**Dependencies:** EP-06A.

**Current modules to map and later delete or reduce to policy adapters:**

```text
core_memory/runtime/dreamer/
Dreamer-specific proposal/review/state stores
Dreamer-specific queue handlers
```

**Implementation:**

1. Inventory every Dreamer output kind, trigger, prompt, evidence input,
   review step, and persistence field.
2. Map semantic outputs to typed Artifact kinds; map navigation-only outputs to
   projection metadata rather than artifacts.
3. Replace feature-specific synthesis with `synthesize_artifact` and a
   `dream`/lesson/principle content schema.
4. Replace Dreamer review state with generic artifact review and verification.
5. Route scheduled work through the unified jobs table.
6. Preserve evidence references and limitations; reject legacy content without
   recoverable support from automatic acceptance.
7. Compare target artifact output against the Dreamer evaluation subset, not
   against legacy volume.
8. Leave only a small policy module if “Dreamer” remains a product-facing
   cadence/profile name.

**Exit gate:** Dreamer produces no canonical content through deterministic
generators or feature-specific stores; output quality meets artifact gates.

**Rollback:** disable Dreamer artifact policy. Do not re-enable deterministic
generation for target tenants.

### EP-06C — Goals, Storylines, and Worldlines on Artifacts

**Objective:** Consolidate goal and narrative synthesis while preserving their
domain-specific schemas and temporal behavior.

**Dependencies:** EP-06A; may run in parallel with EP-06B.

**Add:**

```text
core_memory/artifacts/schemas/goals.py
core_memory/artifacts/schemas/storylines.py
core_memory/artifacts/policies/goals.py
core_memory/artifacts/policies/storylines.py
tests/artifacts/test_goal_evidence.py
tests/artifacts/test_storyline_temporality.py
```

**Goal schema:** desired outcome, status assertion, horizon when evidenced,
supporting observations, conflicting evidence, limitations, and explicit
adoption/abandonment Revisions. Repetition of a topic does not establish a goal.

**Storyline/worldline schema:** bounded theme or trajectory, participants,
supporting bead/assertion/association IDs, valid interval when evidenced,
contradictions, unresolved branches, and limitations. Chronology alone does not
establish causality or narrative significance.

**Implementation:** redirect every goal/storyline creator, updater, and reader;
replace mutable status with successor artifacts/assertions and Revisions;
replace separate schedules with typed jobs; provide compatibility reads from
current artifact projections during the window.

**Exit gate:** adversarial repeated-keyword case creates no unsupported goal;
goal/status changes and storyline changes append revisions; no separate
canonical goal/storyline store is written.

**Rollback:** pause policies and expose existing target artifacts; no mutation
rollback is necessary.

### EP-06D — SOUL Artifacts and Rebuildable `SOUL.md` Projection

**Objective:** Make identity, values, principles, tensions, and SOUL text
LLM-authored artifacts whose file representation is disposable.

**Dependencies:** EP-06A, EP-06B, EP-06C.

**Current modules to redirect:**

```text
core_memory/soul/
SOUL generators/writers/review stores
direct SOUL.md mutations
```

**Implementation:**

1. Define typed artifact schemas for identity, value, principle, tension, and
   `soul_section`.
2. Require evidence sets, limitations, synthesis receipt, and configured review
   policy.
3. Model conflicting identity/value evidence as contest or tension, not silent
   overwrite.
4. Append replacements plus Revisions for accepted changes.
5. Build `SoulProjection` that resolves current accepted artifacts and renders
   deterministic Markdown framing around LLM-authored content.
6. Include a generated header containing tenant/workspace, projection
   watermark, renderer version, artifact IDs, and warning that the file is not
   canonical authority.
7. Treat direct file edits as external human SourceEvents; never ingest them by
   mutating artifacts in place.
8. Delete and rebuild the file in tests, proving identical normalized content
   from SQL.

**Exit gate:** provider failure writes no SOUL semantic text; every section is
traceable to artifacts/evidence/receipts; `SOUL.md` rebuild succeeds from SQL.

**Rollback:** delete or stop refreshing the projection file; canonical
artifacts remain available.

### Stage 6 completion gate

- [ ] All artifact kinds use one persistence/revision/review lifecycle.
- [ ] Every artifact semantic field is LLM- or human-authored and receipted.
- [ ] Missing semantics creates no placeholder artifact content.
- [ ] Dreamer, goal, storyline, and SOUL canonical feature stores are no longer
      written on the target path.
- [ ] Artifact replacement is append-only.
- [ ] `SOUL.md` is disposable and rebuildable.

---

## 14. Program Stage 7 — Lossless Promotion and Hot Context

### EP-07A — LLM Context Assembly and `ContextViewRevision`

**Objective:** Preserve promotion as a relative-value semantic operation while
ensuring it cannot alter archive truth.

**Dependencies:** EP-03C, EP-05C, EP-02C.

**Add:**

```text
core_memory/domain/context.py
core_memory/semantic/operations/assemble_context.py
core_memory/jobs/handlers/assemble_context.py
core_memory/api/context.py
tests/context/test_context_revision.py
tests/semantic/test_promotion_ranking.py
```

**Operation input:** current task/session context, candidate bead metadata and
evidence-bounded summaries, current claims/artifacts when authorized,
associations, prior ContextViewRevision, pinned policy requirements, and token
budget metadata.

**LLM output:** ordered bead IDs, per-bead representation tier
`expanded|compressed|omit_from_hot_view`, artifact/claim selections, concise
context rationale, unpack recommendations, limitations, and abstention when the
available evidence is insufficient to rank responsibly.

`omit_from_hot_view` never deletes or supersedes a bead. It means the current
view contains no stub because the LLM selected other evidence within budget.

**ContextViewRevision:** immutable view ID/lineage, scope and task context,
ordered selections, requested tiers, prior view ID, semantic receipt, budget,
policy pins, and creation ledger watermark. Updating a view appends a successor
and `replace_context_view` Revision.

**Hard boundary:** promotion output cannot carry current-truth state, assertion
confidence changes, evidence changes, or bead mutation instructions.

**Exit gate:** promotion ranking is LLM-authored and append-only; archive rows
are unchanged under every promotion operation.

**Rollback:** stop creating new views and use direct archive/retrieval APIs; no
beads need restoration.

### EP-07B — Deterministic Token Packer, Compressed Stubs, and Unpack

**Objective:** Convert the LLM-authored order into a bounded representation
without making semantic ranking decisions.

**Dependencies:** EP-07A.

**Add:**

```text
core_memory/context/packer.py
core_memory/context/render.py
core_memory/projections/hot_context.py
core_memory/api/beads.py
tests/context/test_lossless_packing.py
tests/context/test_unpack.py
tests/property/test_context_packer.py
```

**Compressed stub minimum:** bead ID, observation label/type, canonical
association references available at the view watermark, archive lookup hint,
and a truncation marker. No synthetic summary is created by the packer.

**Packing algorithm:**

1. Reserve mandatory deterministic framing and caller-pinned policy tokens.
2. Traverse the LLM order exactly.
3. Render requested expanded form if it fits.
4. If it does not fit, use the requested compressed stub where allowed.
5. Stop or omit lower-ranked entries when neither representation fits.
6. Record actual representation, token count, omissions, and reasons in the
   projection receipt.

The packer may not reorder items, promote an unselected item, summarize content,
or infer that a bead is more important. Mandatory caller pins are visible in
the semantic operation input and output audit.

**Unpack APIs:**

```text
GET  /v1/beads/{bead_id}
POST /v1/context-views/{view_id}/unpack
GET  /v1/context-views/{view_id}
GET  /v1/context-views/{view_id}/diff/{prior_view_id}
```

Unpack authorizes against the full SQL archive and returns the immutable bead,
evidence links, associations, current assertion resolutions, and projection
watermarks. It does not depend on hot-context files.

**Evaluation:** compare downstream task success against full-context baseline,
token reduction, important-evidence omission, unpack success, and latency.

**Exit gate:** every compressed ID unpacks; packer properties pass; target task
quality is non-inferior within the reviewed margin while reducing tokens.

**Rollback:** discard the hot-context projection and rebuild or retrieve full
beads directly.

### Stage 7 completion gate

- [ ] LLM authors relative value and representation choice.
- [ ] Deterministic packing preserves the returned order.
- [ ] Compression changes no canonical bead, truth, or evidence.
- [ ] Every stub contains ID, label/type, associations, and lookup ability.
- [ ] Views are append-only, diffable, and auditable.
- [ ] Downstream task evaluation meets the Stage 0 threshold.

---

## 15. Program Stage 8 — One Complete Retrieval Pipeline

### EP-08A — RetrievalPlan and Unified Neutral Collectors

**Objective:** Establish one query entry point and make the LLM select the
retrieval strategy while retaining all capabilities.

**Dependencies:** EP-04C, EP-05C, EP-07B, EP-02C.

**Add:**

```text
core_memory/retrieval/models.py
core_memory/retrieval/pipeline.py
core_memory/retrieval/collectors.py
core_memory/semantic/operations/plan_retrieval.py
tests/retrieval/test_retrieval_plan.py
tests/retrieval/test_collectors_are_neutral.py
```

**Current modules to converge:**

```text
core_memory/retrieval/pipeline/canonical.py
core_memory/retrieval/agent.py
core_memory/causal_recall.py
search/recall/trace endpoint-specific pipelines
```

**RetrievalPlan output:** interpreted question, target object/time scopes,
requested capabilities, collector budgets, causal/association depth, hydration
targets, resolution requirements, evidence sufficiency criteria, iteration
budget, synthesis policy, and citation policy.

**Available capabilities are always registered:** semantic vector search,
lexical/source lookup, bead/archive lookup, claim scope lookup, artifact lookup,
current-state resolution, association/causal expansion, source hydration, and
hot-context hints. A deterministic keyword gate cannot remove a capability.

**Collectors:** accept explicit plan parameters and return typed candidates with
collector scores, provenance, and watermarks. Scores rank candidate inspection;
they do not establish relevance, support, causality, or truth.

**Effort tiers:** `quick|standard|deep` set budgets/latency ceilings. They never
change semantic authority or install fallback answers.

**Exit gate:** all capability combinations can be represented by one plan;
collectors never synthesize answer text or bypass the resolver.

**Rollback:** keep target endpoint unpublished while collector correctness is
validated.

### EP-08B — Current-State Resolution and Causal/Association Expansion

**Objective:** Resolve canonical truth state before graph expansion and expose
ambiguity instead of silently selecting an edge or claim.

**Dependencies:** EP-08A.

**Add:**

```text
core_memory/retrieval/expansion.py
core_memory/retrieval/resolution.py
tests/retrieval/test_resolution_before_expansion.py
tests/retrieval/test_causal_expansion.py
```

**Stage order:** candidate assertions/artifacts -> `CurrentStateResolver` ->
eligible terminal objects -> association expansion -> inverse traversal ->
causal frontier -> dedupe by canonical ID -> return evidence graph.

**Rules:**

- ambiguous/contested/invalid claim scopes remain labeled in the evidence graph;
- a graph projection may accelerate discovery only within its watermark;
- canonical edge details and revision state are re-read from SQL before use;
- inverse traversal preserves canonical edge ID and flips display predicate only;
- causal expansion follows only accepted causal predicates, never temporal
  proximity or a collector score;
- bounded cycle detection and visited-set logic are mechanical;
- expansion limits and truncation are returned to the evidence judge.

**Exit gate:** causal benchmark cases require actual accepted causal evidence;
unresolved claims surface as unresolved; projection/direct expansion agree.

**Rollback:** disable graph expansion in the unpublished target pipeline; do
not substitute old causal answers.

### EP-08C — Source Hydration, Iterative Evidence Judgment, and Sufficiency

**Objective:** Allow the LLM to judge evidence using original authorized source
material and request bounded additional retrieval.

**Dependencies:** EP-08B.

**Add:**

```text
core_memory/retrieval/hydration.py
core_memory/retrieval/evidence.py
core_memory/semantic/operations/judge_evidence.py
core_memory/jobs/handlers/hydrate_source.py
tests/retrieval/test_source_hydration.py
tests/retrieval/test_iterative_judgment.py
tests/security/test_hydration_authorization.py
```

**Hydration targets:** SourceEvent payloads, transcript spans, tool results,
file/blob ranges, external source handles, and artifact evidence. Hydration is
tenant/workspace/source-authorized and content-minimized.

**Cache:** operational, encrypted where configured, TTL-bound, keyed by source
version/content identity, and never canonical evidence authority. Responses
retain the canonical EvidenceRef and disclose unavailable/stale hydration.

**Evidence judgment output:** relevant/irrelevant/uncertain candidates,
support/contradiction/context roles, insufficiencies, unresolved ambiguity,
source-quality limitations, requested next collectors/hydrations, and a
`sufficient|insufficient|blocked` decision.

**Iteration:** the orchestrator executes only plan-allowed capabilities within
attempt, token, latency, cost, and frontier bounds. The LLM chooses meaning-
bearing next steps. Code enforces budgets and dedupe. Exhausted budget yields
qualified insufficiency, not a guessed answer.

**Hydration failure:** report source unavailable or unauthorized; the judge may
use remaining evidence with explicit limitations or abstain. A failed fetch
cannot be represented as verified evidence.

**Prompt injection:** hydrated content is delimited as untrusted evidence and
cannot override operation policy. Adversarial documents test instruction
confusion, false citations, and cross-tenant handles.

**Exit gate:** evidence judgment requests appropriate additional work, respects
authorization/budgets, and treats missing source as a limitation.

**Rollback:** disable remote hydration adapters; pipeline returns blocked or
limited state rather than fallback content.

### EP-08D — Answer Synthesis, Citation Verification, and API Convergence

**Objective:** Complete one retrieval path that returns an evidence-grounded
answer or explicit abstention, then route all public read/search/recall paths to
it.

**Dependencies:** EP-08C.

**Add:**

```text
core_memory/semantic/operations/synthesize_answer.py
core_memory/retrieval/citations.py
core_memory/api/retrieval.py
tests/retrieval/test_synthesis_abstention.py
tests/retrieval/test_citation_verification.py
tests/integration/test_retrieval_end_to_end.py
```

**Public contract:**

```text
POST /v1/retrieve
GET  /v1/retrievals/{operation_id}
```

**Response:** answer or abstention, cited evidence/object IDs and source spans,
resolution states, ambiguity/contest warnings, unavailable-source limitations,
plan/effort summary, truncation, projection watermarks, semantic operation IDs,
and freshness class.

**Synthesis:** the LLM receives only judged evidence and disclosed limitations.
It must distinguish observation, current resolved assertion, contested claim,
artifact synthesis, and inference. Unsupported questions abstain or qualify.

**Citation verifier:** deterministic code verifies cited object exists, belongs
to tenant/workspace, was in the judged evidence set, cited coordinates are
valid, and source version matches. Semantic entailment remains an LLM/evaluation
responsibility. A mechanically invalid citation rejects and retries synthesis.

**Convergence:** redirect every public search, recall, causal answer, trace,
hydrated answer, and agent recall entry point to the same orchestrator.
Compatibility wrappers translate inputs/outputs only. Raw administrative
candidate/search APIs remain explicitly namespaced and cannot present results
as answers.

**Exit gate:** all public answer paths invoke one pipeline; semantic, causal,
current-state, and hydrated benchmarks pass; no deterministic answer fallback
exists.

**Rollback:** per-tenant endpoint routing may return to legacy only before final
cutover. Target retrieval failures remain explicit and never call an invisible
fallback.

### Stage 8 completion gate

- [ ] One RetrievalPlan controls all target queries.
- [ ] All required capabilities remain available at every effort tier.
- [ ] Resolver runs before truth-bearing evidence use.
- [ ] Causal expansion uses accepted canonical edges.
- [ ] Hydration is authorized and limitations are visible.
- [ ] LLM judges relevance/sufficiency and authors answers/abstention.
- [ ] Every citation passes mechanical verification.
- [ ] All public retrieval paths converge on one orchestrator.

---

## 16. Program Stage 9 — Projection, Worker, Configuration, and Boundary Consolidation

### EP-09A — Projection Framework, Watermarks, and Rebuild Drills

**Objective:** Put every derived index/view behind one replayable projection
protocol.

**Dependencies:** EP-06D, EP-07B, EP-08D.

**Add:**

```text
core_memory/projections/base.py
core_memory/projections/registry.py
core_memory/projections/runner.py
core_memory/jobs/handlers/project_ledger.py
tests/projections/test_watermarks.py
tests/projections/test_delete_rebuild.py
```

**Projection contract:** kind/schema version, tenant scope, accepted ledger
kinds, current watermark, batch apply, idempotent row apply, rebuild, verify,
health, and staleness report.

**Required registered projections:** current-state, search/vector, graph,
artifact-current, hot-context, SOUL render, and any integration mirror explicitly
classified as non-authoritative.

**Replay:** consume ledger positions monotonically, commit projection mutations
and watermark atomically, tolerate gaps for irrelevant row kinds, and detect
schema/version mismatch. Rebuild creates a new projection namespace/version,
verifies counts/hashes/sample queries, then swaps the operational pointer.

**Read degradation:** APIs compare required consistency class to watermark lag.
They may read canonical SQL, wait within budget, or return stale/pending. They
must not present stale projection state as confirmed current truth.

**Drills:** delete each projection in an isolated environment; rebuild entirely
from canonical SQL; compare normalized rows, queries, and watermarks; prove no
JSONL/index input was read.

**Exit gate:** every projection has a watermark and successful delete/rebuild
drill; projection mutation cannot change canonical query results.

**Rollback:** swap operational pointer to the prior projection version or use
canonical reads while rebuilding.

### EP-09B — Legacy Queue Migration to One Worker Executable

**Objective:** Eliminate separate queue protocols and independent worker state
machines.

**Dependencies:** EP-09A, EP-01E.

**Current mechanisms to migrate:**

```text
core_memory/runtime/queue/jobs.py
core_memory/runtime/queue/side_effect_queue.py
core_memory/runtime/queue/compaction_queue.py
association candidate/review queues
Dreamer/SOUL feature schedules
projection-specific worker loops
```

**Implementation:**

1. Inventory each legacy job kind, trigger, payload, retry behavior, terminal
   state, and downstream obligation.
2. Define a target job kind/version and handler or prove the work is obsolete.
3. Move eligibility into transactional fan-out or reconciliation rules.
4. Convert queued legacy items through a one-time importer with stable dedupe
   keys; do not execute both queue items.
5. Run one worker executable with handler allowlists and priority classes.
6. Scale by replicas and queues-as-query filters, not separate tables/protocols.
7. Publish obligation coverage by subject kind and ledger watermark.
8. Disable legacy producers first, drain/import remaining items, then disable
   legacy consumers.
9. Add architecture tests prohibiting new legacy queue imports.

**Coverage proof:** for a representative ledger range, compute expected
mandatory obligations independently and show every one is terminal or eligible;
zero silent gaps after reconciler.

**Exit gate:** no target feature produces or consumes a legacy queue; all tasks
are visible in one jobs table and worker protocol.

**Rollback:** before cutover, restore producer routing and ignore imported target
jobs by dedupe namespace. After target canonical cutover, rollback is worker
version rollback, not legacy queue resurrection.

### EP-09C — Typed Configuration and Integration Boundary Cleanup

**Objective:** Remove configuration entropy that changes truth invariants and
prevent integrations from bypassing canonical services.

**Dependencies:** EP-09B, EP-03C, EP-08D.

**Add:**

```text
core_memory/config/models.py
core_memory/config/presets.py
core_memory/config/loader.py
tests/config/test_invariants_are_not_flags.py
tests/architecture/test_integration_boundaries.py
docs/configuration/observation-ledger.md
```

**Typed groups:** ledger, jobs, semantic providers/routes, projection adapters,
retrieval budgets, artifact policies, promotion budgets, security/privacy,
observability, and migration mode.

**Allowed deployment presets:** local-inline, local-delegated, hosted, test, and
migration-audit. Presets choose concrete adapters and budgets; none can permit
deterministic semantic fallback, required assertions, mutable canonical rows,
latest-wins resolution, permanent JSONL authority, or skipped mandatory jobs.

**Environment policy:**

- one documented variable per setting;
- names map to typed groups;
- unknown variables in the Core Memory namespace warn or fail according to
  strict mode;
- secrets never enter receipts or logs;
- deprecated variables emit caller-visible startup warnings and metrics;
- invariant-changing legacy flags are ignored only after failing startup with
  an actionable migration message—never silently reinterpreted.

**Integration boundary:** top-level SDK exposes observe, operation status, bead
lookup/unpack, assertion history/current, artifact operations, context, and
retrieve. Integrations cannot construct stores, write indexes, insert canonical
rows, call feature generators, or resolve claims themselves.

**Exit gate:** configuration compatibility audit is complete; target behavior
is stable across presets; integrations pass boundary tests.

**Rollback:** a preset can choose a prior target adapter implementation, but no
configuration may restore a prohibited invariant after tenant cutover.

### Stage 9 completion gate

- [ ] All projections are registered, watermarked, disposable, and rebuildable.
- [ ] All deferred target work uses one jobs table and worker executable.
- [ ] Reconciliation shows zero missing mandatory obligations.
- [ ] Typed configuration contains no invariant-changing option.
- [ ] Integration adapters use only the small public service surface.
- [ ] Legacy queues and feature workers are inactive.

---

## 17. Program Stage 10 — Legacy Import, Canary, Cutover, and Deletion

### EP-10A — Production Importer and Provenance-Preserving Transforms

**Objective:** Import recoverable legacy observations and semantics without
granting unsupported authority.

**Dependencies:** EP-00C, EP-09C.

**Add:**

```text
core_memory/migration/importer.py
core_memory/migration/transforms/
core_memory/migration/checkpoints.py
core_memory/migration/report.py
scripts/migrate_to_observation_ledger.py
tests/migration/fixtures/
tests/migration/test_importer_idempotency.py
tests/migration/test_provenance_preservation.py
```

**Modes:**

```text
inspect
plan
import-to-staging
verify
resume
rollback-export
```

There is no mode that makes JSONL and SQL permanent co-authorities.

**Import sequence per tenant:**

1. acquire tenant migration lease and verify manifest hash;
2. create verified database backup and legacy file snapshot/manifest;
3. import reconstructable SourceEvents with original source identity and times;
4. import observed beads as versions tied to source evidence where possible;
5. assign `provenance_class` without upgrading authority;
6. quarantine deterministic-derived or unknown semantics when evidence cannot
   support target authority;
7. import LLM-authored semantics with known receipts as canonical historical
   rows, preserving model/prompt metadata;
8. import unreceipted LLM semantics as legacy provenance and schedule optional
   re-authoring/review rather than silently verifying them;
9. import claims as assertions and explicit known revisions; leave incompatible
   unlinked terminals ambiguous;
10. import associations once in canonical direction when direction is reliable;
11. schedule LLM re-review when legacy direction/predicate is ambiguous;
12. map Dreamer/goals/storylines/SOUL into artifacts with recoverable evidence;
13. treat mutable index/vector/graph/hot files as projection inputs only for
   comparison, never canonical import;
14. import eligible outstanding work into unified jobs with stable dedupe keys;
15. record legacy ID -> target ID mappings and per-row transform decisions;
16. checkpoint every bounded batch and make resume idempotent.

**Derived companion beads:** map recoverable semantic content into assertions,
associations, or artifacts under legacy provenance; do not retain multiple
canonical observation beads for one SourceEvent merely to preserve the old
shape.

**Unrecoverable source:** retain a quarantined legacy record or explicit source
handle according to policy. Do not fabricate EvidenceRefs.

**Exit gate:** all migration fixture classes import idempotently; counts/hashes,
ID maps, quarantine reasons, and authority classes reconcile.

**Rollback:** before canonical cutover, drop the staging tenant namespace after
report/archive preservation. Never mutate legacy input during import.

### EP-10B — Shadow Reads, Differential Reports, and Rehearsal

**Objective:** Verify the target on real tenant data without creating two live
write authorities.

**Dependencies:** EP-10A.

**Shadow posture:** freeze a legacy snapshot, import it, then replay a bounded
captured SourceEvent log into SQL through the target pipeline. Compare reads and
quality offline or asynchronously. Production requests still have exactly one
configured write authority during each rehearsal interval.

**Differential reports:**

- source and bead archive count/hash reconciliation;
- one-event/one-bead violations;
- assertion scope and ambiguity differences;
- association direction/predicate differences;
- artifact evidence/review differences;
- hot-context task outcome and token differences;
- retrieval answer, citation, abstention, and latency differences;
- job obligation coverage and projection watermark lag;
- tenant isolation and authorization failures;
- legacy semantics quarantined or scheduled for re-authoring.

Differences are classified as expected design correction, target defect,
legacy defect, source unrecoverable, or evaluation dispute. “More rows” is not
a quality win.

**Rehearsals:** full import, interrupted resume, projection delete/rebuild,
worker crash recovery, provider outage/recovery, backup restore, cutover, and
rollback-export. Time and resource consumption are recorded.

**Exit gate:** at least two successful rehearsals on representative datasets;
no unexplained canonical count/hash differences; target meets semantic and
operational gates.

**Rollback:** discard rehearsal target namespace and correct importer/runtime.

### EP-10C — Tenant Canary and Production Cutover

**Objective:** Move production authority to SQL through an observable,
reversible, tenant-scoped runbook.

**Dependencies:** EP-10B and all Stage 0-9 gates.

**Add:**

```text
docs/runbooks/observation-ledger-cutover.md
docs/runbooks/observation-ledger-rollback.md
scripts/verify_observation_ledger_canary.py
tests/release/test_canary_manifest.py
```

**Pre-cutover:**

1. identify explicit tenant/workspace and owners;
2. verify source manifest, backups, schema/app versions, provider routes, worker
   capacity, projection versions, and rollback-export destination;
3. drain or checkpoint legacy writers and jobs;
4. import final legacy delta while writes are bounded/frozen;
5. reconcile counts/hashes and job obligations;
6. run invariant, security, and semantic smoke tests;
7. obtain explicit go/no-go approval.

**Authority switch:** atomically change the tenant routing record from
`legacy` to `canonical_sql`. Start target workers, enable target public APIs,
and keep legacy state read-only. Do not dual-write.

**Canary proof sequence:**

1. live SourceEvent capture and capture receipt;
2. real LLM annotation and semantic receipt;
3. zero-assertion observation;
4. claim supersession and unlinked ambiguity;
5. directional and bi-temporal association;
6. artifact proposal, acceptance, and replacement;
7. promotion, compression, and unpack;
8. semantic+causal+hydrated retrieval with citations;
9. provider failure, pending state, retry, and recovery;
10. missing job injection and reconciliation;
11. projection deletion and rebuild;
12. assertion that no runtime JSONL/index access occurred.

**Monitoring window:** watch semantic failure/retry, unsupported-output sampled
review, queue age, obligation gaps, ledger/projection lag, resolver states,
hydration failure, citation verification, latency/cost, and security signals.
Expansion requires the full window to pass, not merely endpoint availability.

**Rollback:** stop target writes, export/replay SQL SourceEvents and accepted
semantic records through the explicit rollback tool as supported legacy input,
restore routing only after reconciliation, and preserve SQL as the audit source.
Rollback is time-bounded and practiced. Silent opportunistic dual-write is
prohibited.

**Exit gate:** canary passes every PRD canary item and the monitoring window;
rollback readiness remains verified.

### EP-10D — Delete Legacy Authorities and Close the Program

**Objective:** Remove the mechanisms whose coexistence would preserve semantic
drift and reliability risk.

**Dependencies:** EP-10C broad rollout plus completed rollback window.

**Delete or reduce to explicit offline migration/export tooling:**

- canonical JSONL bead/session writers and readers;
- direct `.beads/index.json` access;
- `JsonFileBackend` and mutable SQLite projection-as-authority behavior in
  `core_memory/persistence/backend.py`;
- duplicate claim/current-state resolvers;
- derived companion bead writers;
- deterministic semantic fallback generators;
- feature-specific Dreamer, goal, storyline, and SOUL stores/generators;
- separate association candidate/coverage authority paths;
- separate recall/search/causal answer pipelines;
- old queue tables/files/workers and independent retry state machines;
- invariant-changing flags and deprecated configuration aliases;
- compatibility adapters after their announced window;
- projection code that cannot expose a ledger watermark or rebuild from SQL.

**Deletion method:**

1. prove zero production callers with code search, import tracing, deprecation
   telemetry, and runtime access counters;
2. remove producers before consumers where residual state must drain;
3. archive schema/data manifests and migration reports, not executable legacy
   authority;
4. delete code, tests that assert obsolete behavior, flags, docs, and dependency
   extras together;
5. tighten architecture allowlists to empty for runtime JSONL/index authority;
6. run the full suite and canary with filesystem traps that fail on prohibited
   access;
7. update canonical architecture, API, configuration, migration, and status
   documentation to the verified shipped state.

**Required zero counts:**

```text
canonical runtime JSONL reads/writes = 0
direct .beads/index.json reads/writes = 0
active deterministic semantic fallback outputs = 0
target callers of duplicate resolver = 0
active legacy queue producers/consumers = 0
mutable canonical semantic updates/deletes = 0
projection-only truth decisions = 0
```

**Exit gate:** the full Definition of Done in the PRD and Section 25 of this
plan passes on local and hosted deployments.

**Rollback:** deletion occurs only after the rollback window. Recovery uses the
tagged pre-deletion release and archived migration inputs in a controlled
incident, not dormant legacy branches in the active runtime.

### Stage 10 completion gate

- [ ] Every tenant is canonical SQL or explicitly not migrated.
- [ ] Migration reconciliation and authority classification are archived.
- [ ] Canary and rollback drills pass.
- [ ] JSONL and mutable index are not live authorities.
- [ ] Duplicate semantic engines, resolvers, queues, and fallbacks are deleted.
- [ ] Documentation describes code that is actually deployed and proven.

---

## 18. Test and Verification Architecture

The test program must prove structural integrity, semantic quality, operational
recovery, and end-to-end product truth separately. A single aggregate green
test job is insufficient.

### 18.1 Test layers

| Layer | Purpose | Provider/database | Required cadence |
|---|---|---|---|
| Domain unit | schemas, vocabulary, time, revisions, resolver | none | every commit |
| Architecture | forbidden imports/authorities/fallbacks | source tree | every commit |
| SQLite contract | canonical transactions and jobs | SQLite | every commit |
| PostgreSQL contract | same behavior, RLS, concurrency | disposable PostgreSQL | every PR |
| Property | revision graphs, replay, idempotency, packing | generated inputs | every PR/nightly by size |
| Recorded semantic | schema/prompt regression without live variability | recorded attempts | every PR |
| Live semantic | actual model faithfulness and abstention | production-like route | semantic PR/nightly |
| Adversarial | hallucination, causality, tenancy, injection | mixed | every PR subset; full nightly |
| Projection drill | delete/rebuild and watermark consistency | both databases | every projection PR/release |
| Migration | fixture inventory/import/reconcile/rollback | isolated copies | every migration PR |
| End-to-end | capture through cited retrieval | real worker/model | release candidate |
| Canary | deployed identity, schema, routing, data | production canary | every rollout |

### 18.2 Test markers

Register and document markers rather than silently skipping:

```text
unit
architecture
sqlite_contract
postgres_contract
property
semantic_recorded
semantic_live
adversarial
projection_rebuild
migration
end_to_end
canary
slow
```

A skipped required test fails its CI job unless the job is explicitly a local
developer subset. Live/provider tests report `blocked` when credentials or a
route are absent; release tooling treats blocked as not passed.

### 18.3 Shared database contract suite

The same parameterized suite executes through each adapter factory and covers:

- schema creation and migration checksum;
- SourceEvent+job atomicity;
- annotation-bundle atomicity and optional-child repairs;
- ID and idempotency replay;
- ledger-position allocation under concurrency;
- canonical update/delete rejection;
- cross-tenant and cross-workspace rejection;
- evidence coordinate and foreign-key integrity;
- revision append and resolver query input;
- concurrent job leasing and token ownership;
- expired-lease recovery;
- deduplicated dependent job fan-out;
- reconciliation after deliberately removed operational obligations;
- projection watermark transactionality;
- backup, restore, and replay.

Adapter-specific tests supplement rather than replace this suite.

### 18.4 Property-test generators

Build generators for:

- valid and invalid revision DAGs;
- compatible/incompatible assertion terminals;
- known-time/valid-time interval combinations;
- canonical associations and inverse traversals;
- repeated job execution at every crash boundary;
- ledger row streams and projection replay batching;
- ContextViewRevision selections and token budgets;
- migration records with duplicate IDs, partial rows, corrupt lines, and missing
  evidence.

Key properties:

1. Resolver output is independent of input order.
2. Every graph either resolves to a named state or `invalid`; it never crashes
   or guesses.
3. Adding an unrelated scope cannot change another scope's resolution.
4. Inverse traversal never writes or requires a second canonical edge.
5. Re-executing a completed job cannot duplicate a semantic row.
6. Replaying a ledger prefix yields identical projection state regardless of
   batch boundaries.
7. A projection mutation cannot change a canonical resolution.
8. The context packer preserves LLM order and every emitted stub is unpackable.
9. Import resume yields the same target IDs/counts/hashes as uninterrupted
   import.

### 18.5 Semantic test harness

Every semantic case stores:

- case ID and corpus version;
- authorized source/evidence payload;
- operation kind/version;
- prompt/policy/schema/vocabulary versions;
- route and model identity;
- producer output and receipt;
- verifier output and receipt when used;
- mechanical validation result;
- rubric scores and adjudicator provenance;
- latency, attempts, tokens, and cost;
- delta from the accepted baseline.

Recorded attempts exercise parsing, validation, retry feedback, and downstream
transactions. They do not count as evidence that the current live model still
meets quality gates.

### 18.6 Required adversarial cases

At minimum, preserve permanent regression cases for:

- a greeting that must not invent a goal, preference, or claim;
- topic repetition that must not become intention;
- agent proposal that must not become user commitment;
- failed tool action that must not become completed work;
- chronological adjacency that must not become causation;
- embedding similarity that must not become a relationship;
- unknown relation direction and unknown valid time;
- absent claim decision that must not become reaffirm/supersede;
- incompatible unlinked claims that must remain ambiguous;
- repetition/salience/myelination that must not raise truth state;
- provider outage during annotation, association, artifact, promotion, SOUL,
  and retrieval;
- invalid optional assertion alongside a valid bead;
- prompt injection inside a hydrated document;
- forged/cross-tenant EvidenceRefs and hydration handles;
- stale projection claiming to be current;
- compressed bead missing from a hot view but successfully unpacked;
- privacy-erased evidence requiring projection rebuild and visible limitation.

### 18.7 End-to-end scenarios

Each release candidate runs these with a real model and both persistence modes
where practical:

1. **Preference change:** observe initial preference, later contrary preference,
   explicit revision, current and as-of retrieval.
2. **Operational state:** observe a time-bounded failure and later resolution;
   verify valid-time versus known-time answers.
3. **Goal lifecycle:** observe adoption, progress, conflict, and abandonment;
   verify no goal before adoption evidence.
4. **Contradictory sources:** ingest independent support and contradiction;
   preserve ambiguity/contest and source roots.
5. **Storyline:** synthesize across sessions with evidence and limitations;
   revise after new evidence.
6. **SOUL:** propose identity/value artifacts, review, replace, delete rendering,
   and rebuild it.
7. **Context pressure:** assemble a constrained hot view, compress beads, run a
   downstream task, then unpack an omitted/full archive record.
8. **Causal retrieval:** plan semantic collection, resolve current state, expand
   accepted causal edges, hydrate source, synthesize with verified citations.
9. **Abstention:** ask an unsupported question and receive explicit
   insufficiency rather than plausible text.
10. **Recovery:** interrupt workers and provider, reconcile jobs, rebuild
    projections, and complete without duplicate semantics.

### 18.8 Standard verification commands

Exact command wrappers may evolve, but the repository must provide stable
entry points equivalent to:

```bash
python -m pytest -q -m "unit or architecture or sqlite_contract"
python -m pytest -q -m postgres_contract
python -m pytest -q -m "property or adversarial"
python -m pytest -q -m semantic_recorded
python scripts/run_observation_ledger_evals.py --suite semantic_live
python -m pytest -q -m projection_rebuild
python -m pytest -q -m migration
python -m pytest -q -m end_to_end
python -m ruff check core_memory tests
python -m mypy core_memory/domain core_memory/ledger core_memory/semantic core_memory/jobs core_memory/retrieval
```

If the project selects a different formatter/type checker, update this section
and CI together. A tool absence must not be confused with a passing check.

---

## 19. CI and Release Pipeline

### 19.1 Required checks

Every PR after EP-01B requires:

```text
lint-and-types
architecture-contract
domain-unit
sqlite-contract
postgres-contract
semantic-recorded
adversarial-fast
migration-fixtures (when migration code or schema changes)
projection-rebuild (when ledger/projection code changes)
```

Semantic operation PRs additionally require a linked live-evaluation run.
Release candidates additionally require full adversarial, full property,
end-to-end, backup/restore, migration rehearsal, and deployed canary reports.

### 19.2 Schema-change gate

A canonical schema change includes:

- dialect migrations;
- updated schema manifest/checksum;
- adapter changes;
- shared contract tests;
- migration fixture changes;
- backup/restore compatibility assessment;
- projection replay assessment;
- rolling deployment order;
- rollback/forward-fix plan.

Schema changes merge before dependent application code unless the application
is strictly tolerant of both schema states and this is proved in tests.

### 19.3 Semantic-change gate

A prompt, operation schema, vocabulary, routing, verification, or model policy
change includes:

- new immutable version ID;
- human-readable semantic diff;
- recorded suite result;
- live suite result;
- unsupported-output and abstention deltas;
- latency/token/cost deltas;
- re-authoring or re-review decision for existing objects;
- rollback route to the prior semantic version.

Changing the default model without the same report is a semantic change, not an
operational toggle.

### 19.4 Migration-change gate

Importer changes require immutable before/after fixture manifests, stable ID
mapping proof, resume/idempotency proof, provenance-classification report, and
explicit accounting for newly quarantined or promoted rows.

### 19.5 Release artifacts

Each release candidate publishes:

- source commit and dependency lock hash;
- schema dialect/version/checksums;
- semantic operation/prompt/schema/vocabulary/model policy versions;
- projection schema versions;
- evaluation corpus and results;
- migration tool and mapping version;
- canary tenant/run IDs;
- known limitations and failed/blocked gates;
- rollback-compatible application and schema versions.

---

## 20. Observability and Audit Implementation

### 20.1 End-to-end correlation

Carry these identifiers through structured logs, traces, metrics exemplars, API
receipts, and audit queries:

```text
request_id
tenant_id / workspace_id (appropriately protected)
source_event_id
operation_id
semantic_attempt_id
job_id / lease token hash
canonical object IDs
ledger_position
projection kind / watermark
retrieval_id
```

Never log raw secrets, provider credentials, unrestricted source payloads, or
hidden chain-of-thought. Semantic rationale stored by contract must be concise,
evidence-facing justification, not private reasoning traces.

### 20.2 Core operational metrics

Use stable names and dimensions. Minimum logical metrics:

```text
core_memory_events_captured_total
core_memory_annotation_completion_total{state}
core_memory_semantic_attempts_total{operation,route,outcome,error_class}
core_memory_semantic_latency_seconds{operation,route}
core_memory_semantic_tokens_total{operation,direction}
core_memory_jobs_total{kind,state}
core_memory_job_oldest_eligible_seconds{kind}
core_memory_job_lease_expired_total{kind}
core_memory_obligation_gaps{kind}
core_memory_ledger_position{tenant}
core_memory_projection_watermark{tenant,projection}
core_memory_projection_lag_positions{projection}
core_memory_resolution_total{state,kind}
core_memory_association_coverage_ratio{stage}
core_memory_retrieval_total{outcome,effort}
core_memory_retrieval_citation_reject_total{reason}
core_memory_hydration_total{outcome,source_kind}
core_memory_migration_records_total{kind,disposition}
core_memory_legacy_authority_access_total{path_class,operation}
```

High-cardinality object IDs belong in traces/audit, not metric labels.

### 20.3 Semantic quality metrics

Operational dashboards distinguish:

- schema-invalid output;
- evidence-invalid output;
- verifier rejection;
- unsupported semantic output from sampled adjudication;
- appropriate versus inappropriate abstention;
- zero-assertion rate by source kind;
- claim revision action accuracy;
- association acceptance precision and direction accuracy;
- artifact evidence/limitation completeness;
- promotion downstream task success and token reduction;
- retrieval citation precision, answer support, and abstention quality.

Zero assertions, rejected association candidates, and retrieval abstention are
not automatically failure metrics. Their appropriateness is measured against
the corpus and sampled review.

### 20.4 Required audit views

Provide operator/admin queries for:

1. SourceEvent -> annotation jobs -> attempts -> bead/assertions -> dependent
   jobs -> projections;
2. canonical object -> evidence -> semantic receipt -> model/prompt/schema;
3. scope key -> assertions -> Revision graph -> current resolution;
4. association candidate -> review decision -> canonical edge or rejection;
5. artifact lineage -> EvidenceSets -> reviews -> rendering;
6. context view -> semantic ranking -> actual pack -> unpack;
7. retrieval -> plan -> candidates -> resolution -> hydration -> judgment ->
   synthesis -> citations;
8. expected versus actual job obligations;
9. projection watermark and rebuild history;
10. legacy object -> provenance class -> target mapping/disposition.

Audit views are tenant-scoped and authorization-controlled. They return
evidence-facing rationale and state transitions, not private reasoning.

### 20.5 Alerts

Page or urgently notify for:

- nonzero cross-tenant authorization violations;
- canonical immutability trigger attempts;
- backup/restore verification failure;
- unresolved obligation gaps after a reconciliation window;
- projection lag beyond a current-truth consumer's threshold;
- provider outage causing backlog beyond SLO;
- sudden semantic schema-invalid or verifier-reject regression;
- nonzero runtime legacy-authority access after tenant cutover;
- resolver projection/direct disagreement;
- citation-verification regression;
- migration count/hash mismatch.

Ticket/notify at lower urgency for cost drift, increased appropriate pending
work, long-tail hydration failure, stale artifact policy work, and capacity
trends.

---

## 21. Security, Privacy, and Authority Workstream

Security work is embedded in each package and receives a release-wide audit.

### 21.1 Tenant isolation

- tenant/workspace comes from authenticated context, never semantic output;
- PostgreSQL RLS and tenant-qualified foreign keys enforce hosted isolation;
- SQLite APIs still require tenant scope to prevent future multi-tenant drift;
- workers set and clear tenant context transaction-locally;
- provider routing forwards only allowlisted tenant metadata;
- evidence, revision, hydration, projection, migration, and unpack paths all
  have cross-tenant negative tests.

### 21.2 Source minimization

Semantic operations receive the minimum authorized evidence needed for the
operation. Receipts store hashes/IDs and policy-approved excerpts rather than
unrestricted source payload duplication. Provider adapters apply operation-
specific redaction before transmission and record the redaction policy version.

### 21.3 Encryption and secrets

- database/file encryption follows deployment policy;
- hydration cache and backups receive the same or stronger protection as source
  material;
- credentials are injected at composition time and excluded from requests,
  receipts, logs, and migration manifests;
- backup restores are access-controlled and audited;
- delegated routes use scoped credentials and validate returned tenant context.

### 21.4 Prompt injection

All source and hydrated content is marked untrusted. System operation policy,
allowed tools, output schema, tenant scope, and evidence budget cannot be
modified by evidence content. The runtime does not let documents select
providers, retrieve other tenants, or call arbitrary tools.

### 21.5 Privacy deletion and redaction

Append-only semantic history does not override legal deletion. Implement a
privileged, explicit erasure workflow that:

1. resolves exact tenant/source/object scope read-only;
2. produces an impact manifest;
3. obtains required authorization;
4. disables application traffic for the scoped maintenance transaction when
   necessary;
5. removes or cryptographically erases canonical source/semantic material under
   a maintenance role;
6. writes a separate non-sensitive audit event where legally permitted;
7. invalidates/rebuilds every affected projection/cache;
8. marks dependent evidence unavailable so retrieval cannot cite it;
9. verifies backups/retention according to policy.

This exceptional workflow is not a general mutation API and is tested
separately from normal revision behavior.

### 21.6 Human authority

Human correction is welcome but explicit. It enters as a SourceEvent or
review action with actor identity and evidence. It may approve/reject/replace
according to policy, producing immutable records and Revisions. It never edits
canonical rows in place or launders unsupported legacy semantics into observed
fact.

---

## 22. Performance and Capacity Plan

### 22.1 Performance budgets

Stage 0 records current baselines; owners then commit concrete SLOs for:

- SourceEvent capture p50/p95/p99;
- semantic annotation time-to-completion by route;
- canonical bead/assertion lookup;
- current-state resolution by graph size;
- job lease/complete throughput and oldest eligible age;
- projection position lag;
- quick/standard/deep retrieval latency;
- source hydration latency and cache hit rate;
- promotion time and token reduction;
- import rows/second and rehearsal duration.

Quality and invariant gates take precedence over latency. The response to an
unmet semantic latency SLO is async capture, capacity, routing, or budget work—
not deterministic semantic fallback.

### 22.2 Index plan

At minimum, index:

- `(tenant_id, ledger_position)` on every scan envelope or the central envelope;
- SourceEvent idempotency and external identity;
- bead source-event/lineage and evidence foreign keys;
- assertion structural scope keys and subject/predicate;
- association source/target/predicate plus valid interval access;
- Revision predecessor/successor/scope and recorded time;
- artifact kind/lineage/review state;
- job eligibility `(state, eligible_at, priority)` and lease expiry;
- projection watermark and current-state scope;
- hydration source identity/version.

Every new index needs a query-plan example and write-amplification assessment.
Search/vector/graph indexes remain projections and do not substitute for
canonical relational indexes.

### 22.3 Scaling posture

- Scale workers horizontally through one jobs table and short leases.
- Batch projection reads by tenant/ledger position.
- Apply backpressure by job priority and tenant fairness, not dropped
  obligations.
- Bound LLM candidate/evidence inputs and permit iterative retrieval.
- Keep database transactions short and outside model latency.
- Use connection pools only at composition roots with per-transaction tenant
  context.
- Partition PostgreSQL only after measured table/index pressure; partitioning is
  not a Stage 1 requirement and cannot change ledger ordering or resolver
  semantics.

### 22.4 Load tests

Before broad rollout, test:

- concurrent event capture with duplicate idempotency keys;
- sustained annotation backlog and recovery;
- multiple worker replicas leasing mixed job priorities;
- hot tenant fairness versus many small tenants;
- large revision scopes including malformed/cyclic migration input;
- dense candidate collection with low association acceptance;
- projection rebuild while new ledger rows arrive;
- retrieval over large archives with causal expansion/hydration;
- migration under bounded production-sized fixtures;
- SQLite local use during in-process worker activity.

---

## 23. Failure and Recovery Matrix

| Failure | Required behavior | Prohibited behavior | Proof |
|---|---|---|---|
| Semantic provider unavailable | event durable; job retries; pending visible | fallback bead/artifact/answer | outage integration test |
| Invalid LLM schema | correction retry; receipt retained | permissive regex/default decision | semantic contract test |
| Unsupported bead summary | verifier rejection/retry | commit because fields are typed | adversarial eval |
| Invalid optional assertion | bead + valid children commit; repair job | discard valid observation or invent child | transaction test |
| DB unavailable during capture | request fails/retries with no false receipt | claim captured without commit | injected fault |
| DB fails after model call | idempotent job retries same operation | duplicate semantic rows | crash-boundary test |
| Worker crashes while leased | lease expires; safe retry | lost job or duplicate truth | lease fault test |
| Mandatory job absent | reconciler recreates by dedupe key | silent missing coverage | reconciliation test |
| Projection corrupt/stale | isolate/delete/rebuild; disclose lag | treat projection as truth | rebuild drill |
| Resolver sees cycle/broken chain | return `invalid`; alert/quarantine | latest-row winner | property test |
| Multiple unlinked terminals | return `ambiguous` | confidence/salience winner | resolver matrix |
| Hydration unavailable | limitation/blocked/abstain | cite unavailable content as verified | retrieval test |
| Model regression | halt rollout; route prior version; re-evaluate | deterministic fallback | semantic release drill |
| Migration parse failure | quarantine with manifest reason | skip silently | fixture reconciliation |
| Direction unknown in legacy edge | re-review or quarantine | infer from chronology/field order | migration test |
| Backup restore fails | stop cutover; repair recovery | continue migration | runbook drill |
| Privacy erasure occurs | rebuild affected projections; citations unavailable | leave cached/vector copies | erasure test |

---

## 24. Implementation Decision Closure

The PRD lists implementation decisions that do not alter product invariants.
They close as follows:

| PRD implementation choice | Plan decision | Closure PR phase(s) |
|---|---|---|
| Exact observation labels | freeze v1 only after corpus confusion analysis; version all changes | PR-00E through PR-00I / PR-01A |
| Association predicates/qualifiers | freeze canonical forward registry after migration/eval analysis | PR-00F / PR-05B |
| Typed tables vs JSON | typed authority/time/join fields; constrained versioned JSON for variant content | PR-01D / PR-01E |
| Opaque IDs and positions | UUIDv4 plus tenant counter allocated in transaction | PR-01A / PR-01D |
| Invalid optional bundle rows | commit valid bead/children; schedule typed child repair | PR-03C |
| Model/provider policy | versioned per operation; benchmarked; allowlisted tenant routing | PR-02B through PR-02D |
| Artifact verification/review defaults | versioned policy per kind/authority tier; no placeholder content | PR-06A / PR-06B |
| Context cadence/budgets | deployment preset defaults selected from task/token eval | PR-07A / PR-07C |
| Projection staleness | per read consistency class; concrete SLO after load baseline | PR-09A through PR-09D |
| Missing legacy source evidence | quarantine or legacy-limited record; never fabricate evidence | PR-10A |
| PostgreSQL partitioning | defer until measured threshold; preserve logical contract | post-cutover capacity RFC if needed |
| Local in-process workers | enabled by default in local-inline preset; same JobStore/handler protocol | PR-01I / PR-09G |

No table entry may be resolved by introducing deterministic semantic authorship,
dual canonical authorities, required assertions, or lossy promotion.

---

## 25. Program Tracking and Definition of Done

### 25.1 Architectural workstream coverage rollup

The table below rolls up related architecture requirements only. These `EP-*`
rows are **not execution phases and are not pull requests**. Section 6.5 is the
only delivery/status register: its 77 `PR-*` rows map one-to-one to complete
PRs. A workstream becomes complete only when all mapped PR phases have merged
and its stage gate has passed.

| Workstream | Deliverable | Depends on | Primary proof | Rollup state |
|---|---|---|---|---|
| EP-00A | architecture contract | — | violation self-tests | not started |
| EP-00B | semantic corpus/baseline | 00A | independent baseline report | not started |
| EP-00C | legacy inventory | 00A | count/hash authority manifest | not started |
| EP-01A | domain kernel | 00A, 00B input | schema/property tests | not started |
| EP-01B | SQL schemas/migrations | 01A, 00C | dual-dialect schema tests | not started |
| EP-01C | SQLite ledger | 01B | shared contract + backup | not started |
| EP-01D | PostgreSQL ledger | 01B | shared contract + RLS | not started |
| EP-01E | jobs/worker/reconciler | 01C, 01D | lease/crash/gap tests | not started |
| EP-02A | semantic contracts | 01A, 01E | no-fallback schema tests | not started |
| EP-02B | semantic runtime/routes | 02A | retry/receipt/route tests | not started |
| EP-02C | verification closure | 02B, 00B | live smoke/adversarial | not started |
| EP-03A | SourceEvent API | 01E, 02A | atomic capture receipt | not started |
| EP-03B | annotation commit | 03A, 02C | grounding/zero-assertion | not started |
| EP-03C | adapter convergence | 03B | caller inventory/access zero | not started |
| EP-04A | pure resolver | 03B | graph properties/matrix | not started |
| EP-04B | claims/current projection | 04A, 02C | revision flow/parity | not started |
| EP-04C | bi-temporal APIs | 04B | valid/known matrix | not started |
| EP-05A | neutral candidates | 03B, 01E | cannot-write-edge test | not started |
| EP-05B | association review | 05A, 02C, 04A | live direction/no-link eval | not started |
| EP-05C | evidence/coverage/graph | 05B, 04C | independence/rebuild | not started |
| EP-06A | artifact kernel | 03B, 04C, 05C | shared lifecycle | not started |
| EP-06B | Dreamer migration | 06A | artifact eval/no old writes | not started |
| EP-06C | goals/storylines | 06A | evidence/revision eval | not started |
| EP-06D | SOUL projection | 06A-06C | delete/rebuild/no fallback | not started |
| EP-07A | context assembly | 03C, 05C, 02C | LLM ranking/archive unchanged | not started |
| EP-07B | pack/unpack | 07A | properties/downstream task | not started |
| EP-08A | retrieval planning | 04C, 05C, 07B | capability/neutral collector tests | not started |
| EP-08B | resolution/expansion | 08A | ambiguity/causal tests | not started |
| EP-08C | hydration/judgment | 08B | auth/iteration tests | not started |
| EP-08D | synthesis/API convergence | 08C | citations/abstention/E2E | not started |
| EP-09A | projection framework | 06D, 07B, 08D | all delete/rebuild drills | not started |
| EP-09B | worker consolidation | 09A, 01E | zero legacy jobs/gaps | not started |
| EP-09C | config/integration cleanup | 09B, 03C, 08D | preset/boundary audit | not started |
| EP-10A | production importer | 00C, 09C | idempotent provenance import | not started |
| EP-10B | shadow verification | 10A | two full rehearsals | not started |
| EP-10C | canary/cutover | 10B, all gates | deployed canary/runbook | not started |
| EP-10D | legacy deletion | 10C + rollback window | required zero counts | not started |

The atomic `PR-*` phase state belongs in the implementation program's canonical
status document once work starts. It must be updated from complete
push/open/merged/deployed/proven facts, not intention. Workstream rollup state is
derived from those atomic phases and cannot be advanced independently.

### 25.2 Milestone proof vocabulary

Status reporting uses these labels:

```text
specified
code present
unit proved
integration proved
live-semantic proved
deployed
canary proved
cut over
legacy deleted
```

Later labels imply earlier proof only when the same commit/schema/semantic
version was tested. A PR merge does not prove deployment; a deployment does not
prove schema migration; schema presence does not prove capture-to-retrieval;
endpoint output does not prove semantic authority.

### 25.3 Final program gate

The program is complete only when all items below are simultaneously true:

1. Canonical state is SQLite/PostgreSQL, never JSONL or a mutable JSON index.
2. Every accepted SourceEvent atomically schedules annotation.
3. Every successfully annotated event has exactly one observation bead lineage
   and one current immutable version.
4. Assertions are optional and zero-assertion output is first-class.
5. Bead labels/titles/summaries and all other meaning-bearing outputs are
   LLM- or explicit human-authored with evidence provenance.
6. No canonical semantic operation has a deterministic fallback.
7. Failures remain pending/retryable/failed/quarantined rather than invented.
8. Claims and associations are append-only and change through Revisions.
9. One resolver controls current and as-of state.
10. Unlinked incompatible terminals remain ambiguous or contested.
11. Direction and bi-temporal fields are structurally represented and correctly
    traversed.
12. Aggregate evidence preserves support, contradiction, source roots,
    independence uncertainty, and time.
13. Dreamer, goals, storylines, worldlines, lessons, principles, identity,
    values, tensions, and SOUL share one Artifact lifecycle.
14. Artifact replacements append rows and Revisions.
15. Promotion is LLM-ranked, reversible, and lossless.
16. Every compressed bead unpacks from the full SQL archive.
17. One retrieval pipeline provides planning, semantic collection,
    current-state resolution, association/causal expansion, hydration, judgment,
    bounded iteration, synthesis/abstention, and citation verification.
18. No deterministic retrieval answer fallback remains.
19. All deferred work uses one jobs table and worker protocol.
20. Transactional obligations plus reconciliation show complete coverage.
21. Every projection can be deleted and rebuilt from SQL.
22. SOUL/hot-context/vector/graph/search representations are projections, not
    truth authorities.
23. SQLite and PostgreSQL pass the same canonical contracts.
24. Hosted tenant isolation is database-enforced and adversarially tested.
25. Migration preserves provenance and never upgrades heuristic semantics.
26. Runtime JSONL/index code and duplicate semantic authorities are deleted.
27. Semantic, operational, security, performance, migration, and canary gates
    meet reviewed thresholds.
28. Canonical documentation accurately reflects deployed, end-to-end-proved
    behavior.

### 25.4 Final architecture proof

For any durable semantic object selected at random, an operator must be able to
trace:

```text
authorized observed source
  -> EvidenceRef boundary
  -> attributed semantic operation and attempts
  -> validated append transaction
  -> explicit Revision chain when changed
  -> one current-state resolution
  -> optional disposable projections
  -> retrieval judgment and verified citation when used
```

If any link is absent, inferred by an undocumented fallback, stored only in a
projection, or contradicted by another active resolver, the architecture is not
done.

---

## 26. PRD Traceability Matrix

| PRD area | Atomic PR phases | Primary release proof |
|---|---|---|
| Beads as observation notes | PR-01B, PR-03A through PR-03C | one event/one grounded bead |
| Assertions optional | PR-01B, PR-03B, PR-03C | zero-assertion adversarial case |
| LLM semantic authority | PR-02A through PR-02D | no-fallback/live semantic suite |
| One SQL ledger | PR-01D through PR-01G, PR-10H | shared contracts; zero JSONL access |
| Atomic write obligations | PR-01J, PR-03A, PR-03C | crash/reconciliation suite |
| Revision/current truth | PR-04A through PR-04D | resolver properties/as-of matrix |
| Direction and bi-temporality | PR-01A, PR-04D, PR-05B, PR-05C | direction/time benchmark |
| Association coverage/evidence | PR-05A through PR-05F | coverage/independence/graph rebuild |
| One artifact system | PR-06A through PR-06G | shared lifecycle and SOUL rebuild |
| Promotion retained/lossless | PR-07A through PR-07C | archive unchanged/unpack/task eval |
| One complete retrieval | PR-08A through PR-08G | E2E cited answer/abstention |
| Source hydration | PR-08D | authorization/failure/injection tests |
| One jobs system | PR-01H through PR-01J, PR-09E, PR-09F | one table; zero obligation gaps |
| Disposable projections | PR-09A through PR-09D | delete/rebuild drills |
| Small adapter surface | PR-03D through PR-03G, PR-09H | import/caller boundary tests |
| Typed configuration | PR-09G | no invariant-changing flags |
| Migration/no authority upgrade | PR-00C, PR-00D, PR-10A through PR-10G | manifests/rehearsals/canary |
| Legacy deletion | PR-10H through PR-10N | required zero counts |
| Quality program | PR-00B through PR-00T, Sections 18-19 | reviewed baseline and release report |
| Security/privacy | PR-01G, PR-08D, Section 21 | RLS/hydration/erasure suites |
| Reliability/recovery | PR-01H through PR-01J, PR-09A through PR-09D, Section 23 | fault tests and drills |

---

## 27. Recommended First Implementation Slice

Begin with `PR-00A`, then deliver `PR-00B` through `PR-00T` as the twenty
separate complete Stage 0 PRs. After their declared dependencies merge, execute
the Stage 1 phases in Section 6.5 one complete PR at a time. Do not begin by
replacing the existing writer, moving legacy packages, or building a
compatibility dual writer.

The first end-to-end thin slice is complete when `PR-03C` merges:

```text
SourceEvent
  -> SQL transaction + annotate_event Job
  -> real LLM operation
  -> validation and receipt
  -> one grounded bead with zero-or-more assertions
  -> SQL append + mandatory dependent Jobs
```

That slice should be demonstrated with:

- one meaningful user-agent interaction;
- one greeting/low-content interaction with zero assertions;
- one invalid model output corrected by retry;
- one provider outage that remains pending;
- one idempotent replay;
- one SQLite run and one PostgreSQL run;
- an audit trace from event through semantic receipt and ledger rows.

Only after this slice meets the semantic benchmark should production adapters
begin redirecting in `PR-03D` through `PR-03G`. This ensures the program's first
delivered capability directly tests the central thesis: Core Memory is an
evidence-bound observer before it becomes a claim, relationship, artifact,
context, or answer engine.

---

## 28. Mandatory Job Obligation Catalog

The unified jobs table is reliable only if obligation creation is complete.
This catalog is part of the architecture contract, not an illustrative list.
Adding a canonical object or semantic feature requires updating this table,
transactional fan-out rules, reconciliation rules, coverage metrics, and tests.

### 28.1 Job kinds and triggers

| Job kind | Created by | Subject | Meaning-bearing work | Required downstream obligations |
|---|---|---|---|---|
| `annotate_event.v1` | SourceEvent capture transaction | SourceEvent | yes: bead and optional assertions | annotation commit fans out below |
| `repair_annotation_children.v1` | partial annotation commit | Annotation receipt | yes: retry rejected optional children | projections/reviews for repaired rows |
| `project_current_state.v1` | assertion/Revision append | scope key | no: pure resolver projection | none |
| `project_search.v1` | bead/assertion/artifact append or Revision | object/scope | no: index materialization | none |
| `collect_association_candidates.v1` | bead/assertion append, material new evidence, coverage repair | object | no: neutral discovery only | `review_associations.v1` when candidates exist |
| `review_associations.v1` | candidate batch committed | candidate batch | yes: relation/no-link/defer | graph, evidence, policy, context |
| `aggregate_evidence.v1` | assertion/association/evidence Revision | scope/EvidenceSet | yes for inclusion stance when not already authored | current/search/graph as applicable |
| `project_graph.v1` | association/EvidenceSet/Revision append | edge/scope | no | none |
| `evaluate_artifact_policies.v1` | new material evidence, session boundary, scheduled reconsideration | policy scope | yes: whether evidence warrants synthesis | `synthesize_artifact.v1` only on explicit LLM decision |
| `synthesize_artifact.v1` | accepted policy evaluation or explicit request | artifact proposal scope | yes | review/current/search/context/SOUL as applicable |
| `review_artifact.v1` | artifact policy requiring review | Artifact | yes | current/search/context/SOUL as applicable |
| `project_artifact_current.v1` | artifact/Revision/review append | artifact lineage | no: resolver projection | none |
| `render_soul.v1` | accepted current SOUL-relevant artifact change | tenant/workspace | no semantic authorship; Markdown render | none |
| `assemble_context.v1` | session/task boundary, material context change, token pressure, explicit refresh | context scope | yes: ranking/tier choice | `project_hot_context.v1` |
| `project_hot_context.v1` | ContextViewRevision append | context view | no: deterministic pack/render | none |
| `hydrate_source.v1` | RetrievalPlan/judge request or explicit authorized prefetch | EvidenceRef/source handle | no relevance judgment; authorized fetch | resume waiting retrieval operation |
| `continue_retrieval.v1` | async retrieval request or completed dependency | retrieval operation | yes through semantic stages | requested hydration/continuation jobs |
| `reconcile_obligations.v1` | scheduler/administrative request | tenant + ledger range | no; restores declared work | missing jobs only |
| `rebuild_projection.v1` | administrative operation/schema upgrade | projection namespace | no | verification/swap job |
| `verify_projection.v1` | rebuild completion | projection namespace | no semantic authorship | operational pointer swap on success |
| `reauthor_legacy_semantics.v1` | migration disposition requiring new authorship | legacy evidence scope | yes | same fan-out as newly appended target object |
| `verify_migration_batch.v1` | imported batch commit | checkpoint | no semantic authorship | next batch eligibility or quarantine |

`evaluate_artifact_policies.v1` is a semantic operation because deciding that
observed evidence warrants a goal, lesson, storyline, identity statement, or
other artifact is meaning-bearing. Deterministic scheduling only decides when
to ask. The same rule applies when association review returns no link or
promotion returns a sparse context view.

### 28.2 Transactional fan-out matrix

Every canonical append transaction creates these minimum obligations before it
commits:

| Canonical append | Mandatory jobs |
|---|---|
| SourceEvent | `annotate_event.v1` |
| ObservationBead | `project_search.v1`, `collect_association_candidates.v1`, `evaluate_artifact_policies.v1`, `assemble_context.v1` when an active context scope exists |
| ClaimAssertion | `project_current_state.v1`, `project_search.v1`, `collect_association_candidates.v1`, `evaluate_artifact_policies.v1`, active-context refresh |
| Claim Revision | `project_current_state.v1`, `project_search.v1`, `aggregate_evidence.v1`, `evaluate_artifact_policies.v1`, active-context refresh |
| AssociationAssertion | `project_graph.v1`, `aggregate_evidence.v1`, `evaluate_artifact_policies.v1`, active-context refresh |
| Association Revision | `project_graph.v1`, `aggregate_evidence.v1`, artifact-policy evaluation, active-context refresh |
| EvidenceSet | search/graph/current projection jobs for its bound object kinds; artifact-policy evaluation when material |
| Artifact | `project_artifact_current.v1`, `project_search.v1`, active-context refresh, `render_soul.v1` when SOUL-relevant, `review_artifact.v1` when policy requires |
| Artifact Revision/review | artifact-current/search/context jobs and `render_soul.v1` when SOUL-relevant |
| ContextViewRevision | `project_hot_context.v1` |

“When active/material/applicable” is not permission for a heuristic semantic
decision. It refers only to explicit structural facts: an active context scope
exists, the object kind is registered for the projection/policy, the artifact
kind is SOUL-relevant, or the review policy field requires work. Whether new
content or a relationship should exist remains an LLM output.

### 28.3 Dedupe keys

Use stable versioned keys:

```text
annotate:event:{event_id}:op-v1
repair-annotation:{receipt_id}:{rejected-child-hash}:op-v1
project:{projection}:{schema_version}:{object_id}:{object_version}
association-collect:{subject_id}:{collector_version}:{ledger_watermark}
association-review:{candidate_batch_id}:{operation_version}
evidence-aggregate:{scope_key}:{source_watermark}:{operation_version}
artifact-policy:{policy_id}:{scope_key}:{evidence_watermark}:{operation_version}
artifact-synthesize:{proposal_id}:{operation_version}
artifact-review:{artifact_id}:{policy_version}
context-assemble:{context_scope}:{input_watermark}:{policy_version}:{budget_hash}
hydrate:{source_identity}:{source_version}:{authorization_scope_hash}
retrieval-continue:{retrieval_id}:{stage}:{iteration}
reconcile:{tenant_id}:{range_start}:{range_end}:{catalog_version}
projection-rebuild:{tenant_id}:{projection}:{schema_version}:{source_watermark}
migration-verify:{migration_run}:{batch_id}:{transform_version}
```

A changed semantic operation version may intentionally create new work. A
worker retry of the same operation version must reuse the same dedupe and
operation identities.

### 28.4 Scheduling and coverage defaults

Initial operational defaults, subject to measured capacity without weakening
coverage:

- transactionally created jobs are eligible immediately unless their declared
  dependency is incomplete;
- hosted workers long-poll continuously with a target pickup delay under one
  second at normal load;
- local in-process workers poll at most every two seconds while the host is
  active and flush at session finalization;
- expired leases are reclaimed continuously by lease queries and explicitly
  swept at least every minute;
- incremental obligation reconciliation covers new ledger positions at least
  every minute for active hosted tenants and at process/session boundaries
  locally;
- a full tenant obligation sweep runs at least every 15 minutes hosted and at
  startup plus clean shutdown locally until measured scale requires sharding;
- association coverage repair prioritizes unreviewed eligible candidates and
  reports oldest age continuously;
- artifact policy evaluation fires on material registered object appends,
  session finalization, explicit request, and a periodic reconsideration job;
- promotion fires on context creation, task change, session boundary, material
  selected-memory change, token-budget pressure, and explicit refresh;
- projection jobs are immediate and rebuild verification is mandatory before
  a namespace swap;
- failed/quarantined work remains visible and does not count as coverage.

These are operational triggers, not semantic rules. The LLM may validly return
no association, no artifact, no assertion, a sparse context, or retrieval
abstention.

### 28.5 Reconciler algorithm

For each tenant and bounded ledger range:

1. read the catalog version and canonical rows;
2. enumerate mechanically required obligations from row kind, registered
   policies, and active structural scopes;
3. compute each stable dedupe key;
4. compare against pending, leased, succeeded, failed, and quarantined jobs;
5. insert only missing obligations;
6. report terminal failures/quarantines separately from missing jobs;
7. advance the reconciliation checkpoint only after the range is completely
   accounted for;
8. periodically rescan old ranges to detect catalog-version changes or manual
   operational loss.

The reconciler never interprets source text. It can restore “review these
candidates” but cannot create an accepted association; restore “evaluate this
artifact policy” but cannot create a goal; restore “assemble context” but cannot
rank beads.

### 28.6 Coverage invariants

- Every accepted SourceEvent has exactly one terminal or eligible annotation
  obligation.
- Every canonical row has every registered mandatory projection obligation.
- Every eligible association candidate batch has a terminal or eligible review.
- Every policy-triggering evidence watermark has a terminal or eligible policy
  evaluation.
- Every accepted context view has a terminal or eligible hot projection.
- Every failed/quarantined job has a typed reason, attempts, and operator path.
- Obligation gap is zero after the reconciliation SLO; anything else blocks
  release/cutover.

---

## 29. Current-to-Target Module Disposition

This map is refined by EP-00C against the implementation base. It prevents a
new clean subsystem from merely joining the existing set of authorities.

| Current surface | Target owner | Redirect package | Delete package/disposition |
|---|---|---|---|
| `core_memory/persistence/backend.py` JSON/file authority | `core_memory/ledger/*` | 01C/01D/03C | 10D; retain only explicitly scoped export if needed |
| direct `.beads/index.json` readers/writers | Ledger + projections | 03C/09A | 10D |
| session/bead JSONL source-of-truth paths | SourceEvent/ledger | 03C/10A | 10D |
| `runtime/engine.py` write orchestration | `CoreMemoryService` + jobs | 03C | reduce to compatibility adapter, then 10D |
| `runtime/turn/turn_flow.py` semantic branching | SourceEvent adapter + semantic operation | 03C | delete old authorship branches in 10D |
| `runtime/queue/worker.py` | unified `jobs/worker.py` | 09B | 10D |
| `runtime/queue/jobs.py` | unified jobs table | 09B | 10D |
| `runtime/queue/side_effect_queue.py` | job handler registry | 09B | 10D |
| `runtime/queue/compaction_queue.py` | promotion/projection jobs | 07A/09B | 10D |
| `runtime/passes/*` | semantic operations/job handlers/projections | 03C/05B/06A | 10D after caller zero |
| `write_pipeline/*` | SourceEvent + AnnotationBundle pipeline | 03A-03C | 10D |
| `policy/semantic_task_runtime.py` | `semantic/runtime.py` | 02A-02C | compatibility wrapper only, then 10D |
| `schema/semantic_tasks.py` and runtime wrappers | `semantic/contracts.py` registry | 02A | migrate callers, then 10D |
| `claim/resolver.py` | `domain/resolver.py` | 04A-04C | 10D |
| `persistence/store_claim_ops.py` | ledger assertion/revision commands | 04B | 10D |
| `runtime/associations/coverage.py` | candidate/review/coverage services | 05A-05C | 10D |
| `association/preview.py` | neutral candidate collector | 05A | 10D or thin admin preview |
| `association/crawler_contract.py` | jobs + candidate collector | 05A/09B | 10D |
| feature candidate stores | jobs/receipts/operational candidates | 05A/09B | 10D |
| `runtime/dreamer/*` | Artifact policies/operations | 06A/06B | 10D; optional product-name policy remains |
| goal/storyline feature stores | Artifact schemas/projections | 06C | 10D |
| `soul/*` canonical writers | Artifact + SOUL projection | 06D | delete generators/stores in 10D; renderer remains |
| promotion/compaction semantic rules | context operation + packer | 07A/07B | 10D |
| `retrieval/pipeline/canonical.py` | one `RetrievalPipeline` | 08A-08D | 10D or file replaced in place with target only |
| `retrieval/agent.py` | retrieval semantic operations | 08A-08D | 10D |
| `causal_recall.py` | association expansion stage | 08B | 10D |
| endpoint-specific search/recall/trace answers | `/v1/retrieve` wrappers | 08D | wrappers removed after compatibility window |
| vector/graph/hot/SOUL stores treated as authority | projection registry | 09A | authority reads deleted in 10D |
| dispersed `CORE_MEMORY_*` invariant flags | typed configuration/presets | 09C | 10D |

### 29.1 Deletion proof per module

Before deleting a predecessor, attach:

- static import/call-site search;
- runtime deprecation/access telemetry covering the announced window;
- state drain/import reconciliation when applicable;
- replacement contract and end-to-end test;
- configuration and dependency removal;
- documentation and example update;
- architecture allowlist reduction.

A legacy module that remains importable “just in case” is still architectural
surface. If historical file parsing is required after cutover, isolate it under
`core_memory/migration/legacy_readers/`, prohibit production imports, and test
the boundary.

---

## 30. Review Checkpoints

Formal design reviews occur before these merges:

1. **After PR-00T:** approve vocabulary candidates, evaluation thresholds, and
   known baseline failures.
2. **Before PR-01D:** approve SQL logical schema, tenant position allocation,
   immutability, erasure exception, and typed/JSON balance.
3. **Before PR-02B:** approve operation envelope, allowed routing metadata,
   retry/escalation policy, and receipt redaction.
4. **Before PR-03D:** approve the thin-slice live evidence and the first adapter
   routing list.
5. **Before PR-04B:** approve resolver action matrix and ambiguity behavior.
6. **Before PR-05B:** approve canonical predicate/direction registry and
   association benchmark.
7. **Before PR-06B:** approve Artifact kind schemas and review policies.
8. **Before PR-07A:** approve promotion task-quality and token targets.
9. **Before PR-08F:** approve retrieval quality, abstention, citation, latency,
   and source-limitation thresholds.
10. **Before PR-10A:** approve legacy classifications and all no-upgrade rules.
11. **Before PR-10G:** explicit production go/no-go with rollback proof.
12. **Before PR-10H:** confirm rollout and rollback windows, zero legacy access,
    and retained audit/export obligations.

Each review produces a dated decision record linked from the applicable PR
phase.
Silence or code merge is not product approval.
