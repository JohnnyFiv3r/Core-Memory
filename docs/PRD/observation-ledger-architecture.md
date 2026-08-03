# PRD: Core Memory Observation Ledger Architecture

## Single-Ledger, LLM-Authored Semantic Memory With Append-Only Truth, Unified Artifacts, Context Promotion, and Complete Retrieval

**Status:** Draft v1 — proposed target architecture

**Date:** 2026-08-03

**Owner surface:** Core Memory domain model, canonical write path, semantic runtime,
storage, claims, associations, artifacts, promotion, retrieval, background work,
and migration

**Primary invariant:** Every bead documents an observed user-agent event; every
meaning-bearing decision is authored by an LLM constrained to evidence; every
durable semantic change is append-only and resolved through explicit revision
records

**Implementation status:** Not started by this document

**Decision posture:** Product direction accepted; detailed implementation
choices remain subject to the requirements and release gates in this PRD

**Related documents:**

- `agent-led-semantic-write-integrity.md`
- `agentic-semantic-task-runtime.md`
- `dreamer-continuity-engine.md`
- `soul-files.md`
- `myelination-reinforcement.md`
- `docs/contracts/temporal_truth_contract.md`
- `docs/claim_layer.md`
- `docs/architecture_overview.md`
- `docs/design/F-W1-write-pipeline.md`

---

## 0. Document Authority and Supersession

### 0.1 Target-architecture authority

This PRD defines the proposed target architecture for Core Memory. It does not
claim that the current runtime already conforms to the design.

When this PRD conflicts with an older document about implementation mechanism,
this PRD is intended to control after formal approval and implementation
kickoff. Existing user-facing capabilities remain requirements unless this PRD
explicitly removes them.

### 0.2 Mechanisms superseded by this design

This PRD supersedes the following mechanism-level assumptions:

1. JSONL files or `.beads/index.json` serving as live canonical state.
2. Separate semantic runtimes or fallback policies for bead writing, claims,
   associations, Dreamer, goals, storylines, SOUL, and retrieval.
3. Deterministic semantic fallbacks that create labels, summaries, claims,
   relationships, goals, storylines, SOUL text, promotion judgments, or
   retrieval answers.
4. Multiple claim resolvers or competing definitions of current truth.
5. Independent candidate stores and lifecycle engines for each derived
   artifact family.
6. Multiple queue files or worker protocols for semantic and projection work.
7. Promotion as a truth upgrade, destructive retention decision, or lossy
   persistence mechanism.
8. Myelination, recall frequency, or deterministic scoring changing factual
   confidence or semantic authority.
9. Runtime compatibility branches that silently reinterpret legacy semantic
   schemas.

### 0.3 Capabilities preserved

The design preserves:

- one logical observation-bead lineage and one current immutable version for
  every finalized observable event;
- typed labels, titles, summaries, evidence, and source provenance;
- optional atomic claims and explicit supersession chains;
- semantic, causal, directional, and bi-temporal associations;
- deterministic graph and temporal traversal;
- aggregate evidence assessment;
- Dreamer findings;
- goals and their lifecycle;
- storylines and worldline projections;
- SOUL identity, value, tension, and continuity artifacts;
- promotion of relatively valuable beads into expanded hot context;
- reversible compression of lower-value beads into lightweight references;
- semantic/vector search;
- causal and association expansion;
- source hydration and full bead unpacking;
- local SQLite operation and hosted PostgreSQL operation;
- vector and graph indexes as optional projections;
- full audit, replay, as-of queries, retries, and observability;
- all supported host and integration adapters through stable ports.

---

## 1. Executive Summary

Core Memory must become a reliable observation and reasoning engine rather than
a collection of loosely coordinated semantic features.

The current system contains valuable capabilities, but its correctness is
weakened by multiple sources of truth, overlapping state machines, direct
mutation of shared indexes, broad configuration, duplicate resolvers, semantic
fallbacks, and feature-specific pipelines. A missing LLM response can be
replaced by deterministic prose. A malformed claim update can acquire a
default meaning. Two claim resolvers can select different current values. A
Dreamer heuristic can produce an apparently grounded latent goal. These are not
isolated defects; they are consequences of an architecture in which meaning
can enter the system through too many mechanisms.

This PRD consolidates Core Memory around a small set of primitives:

1. **SourceEvent** — immutable documentation of what occurred.
2. **ObservationBead** — an LLM-authored annotation of one SourceEvent.
3. **Assertion** — an optional claim or association supported by beads.
4. **Revision** — an append-only semantic decision that changes current state.
5. **Artifact** — an evidence-bound synthesis such as a goal, storyline,
   Dreamer finding, lesson, or SOUL entry.
6. **ContextViewRevision** — an append-only record of which beads are expanded
   or compressed in hot context.
7. **SemanticTaskReceipt** — provenance for every LLM-authored result.
8. **Job** — the single operational mechanism for deferred work and retries.

All canonical data moves into a transactional SQL ledger. SQLite is the local
implementation and PostgreSQL is the hosted implementation. JSONL and
`.beads/index.json` are fully retired as runtime authorities. Search indexes,
graph stores, hot context, SOUL files, caches, metrics, and integration payloads
become rebuildable projections.

Core Memory uses one semantic-operation runtime. Individual operations may use
different prompts, schemas, and model tiers, but they share one execution,
retry, receipt, validation, and failure contract. The LLM owns conditional
meaning: whether an assertion exists, whether two beads are associated, whether
a claim is superseded, what a storyline means, which beads are relatively
valuable, which evidence matters to a query, and whether an answer is
supported. Deterministic code owns structure: IDs, timestamps, transactions,
schema validation, search execution, graph traversal, temporal math, token
packing, leases, retries, projections, and referential integrity.

The design is intentionally fail-closed. If a semantic operation is unavailable
or invalid, its job remains pending or retryable. Core Memory never manufactures
semantic success through a deterministic fallback.

---

## 2. Product Thesis

### 2.1 Beads are observation notes

A bead is not a free-standing theory about the user. It is the documentation of
an observed interaction involving the user, agent, tools, documents, or an
external operational system.

The LLM acts as a constrained note-taker:

- it observes the bounded event evidence;
- it selects the most appropriate label from a limited vocabulary;
- it writes a faithful title and summary;
- it cites the evidence that supports every meaning-bearing field;
- it abstains from assertions not justified by the event.

A `goal` label is valid when a goal was observed being expressed, adopted,
modified, advanced, blocked, completed, or abandoned. It is not valid merely
because repeated behavior resembles a goal. The latter is an artifact proposal
supported by multiple observations.

### 2.2 Assertions are optional

Every accepted SourceEvent produces an ObservationBead, but it does not need to
produce a claim, association, revision, or artifact.

The absence of an assertion is a legitimate successful result. The system must
never pressure the LLM to invent durable propositions merely to satisfy schema
coverage or richness targets.

### 2.3 Objective truth means evidence-bound current state

Core Memory cannot guarantee external world truth from conversation alone. It
can guarantee that:

1. observations are faithfully represented;
2. assertions are traceable to evidence;
3. inferred or asserted propositions are not mislabeled as independently
   verified facts;
4. semantic changes are explicit and attributable;
5. unresolved contradictions remain visible;
6. current state is computed from one append-only revision ledger;
7. retrieval answers cite the evidence used or abstain.

The product objective is therefore **no ungrounded semantic assertion** and
**no manufactured current truth**.

### 2.4 Simplicity is a reliability feature

Every additional semantic authority, queue, fallback, cache, resolver, or state
machine expands the number of invalid states Core Memory can enter. The target
design prefers:

- one ledger over synchronized files;
- one operation runtime over task-specific invocation machinery;
- one write pipeline over direct and queued variants;
- one revision model over feature-specific mutation semantics;
- one artifact system over independent Dreamer, goal, storyline, and SOUL
  stores;
- one retrieval pipeline over special-case answer paths;
- one jobs table over multiple queues;
- migrations over permanent legacy branches.

---

## 3. Resolved Product Decisions

The following decisions are locked for this scope.

| ID | Decision |
|---|---|
| D-01 | Every accepted observable event produces exactly one logical ObservationBead lineage with exactly one current immutable version. |
| D-02 | Assertions are optional and must not be required bead fields. |
| D-03 | Meaning-bearing fields and conditional semantic decisions are LLM-authored. |
| D-04 | Deterministic semantic fallback is prohibited; failed operations retry. |
| D-05 | SQLite/PostgreSQL form the canonical ledger; JSONL and `.beads/index.json` are retired. |
| D-06 | Canonical semantic records are append-only. Supersession is expressed as a new Revision record. |
| D-07 | A projection may expose `superseded=true`, but original canonical records are not mutated to create that state. |
| D-08 | Claims and associations share provenance and evidence conventions but remain typed assertion variants. |
| D-09 | Relationship direction and bi-temporal mechanics are explicit schema features, not reconstructed semantically downstream. |
| D-10 | Transaction time is system-authored; valid/effective time may be LLM-extracted from evidence. |
| D-11 | Relationships are stored once in a canonical direction; inverse traversal is computed. |
| D-12 | Promotion remains a first-class LLM judgment of relative contextual value. |
| D-13 | Promotion is lossless: all full beads remain in the archive ledger. |
| D-14 | Non-expanded hot-memory beads retain at minimum bead ID, label/type, and associations and can be unpacked. |
| D-15 | Promotion changes representation in hot context, not truth, authority, or archive retention. |
| D-16 | Retrieval uses one pipeline with semantic search, claim-state resolution, causal expansion, association expansion, source hydration, LLM evidence judgment, and answer synthesis. |
| D-17 | The LLM decides retrieval intent and conditional depth; deterministic keyword gates do not disable semantic capabilities. |
| D-18 | All deferred work uses one jobs table and one worker protocol, with multiple worker replicas allowed. |
| D-19 | Transactional job creation and reconciliation sweeps guarantee task coverage. |
| D-20 | Dreamer, goals, storylines, lessons, principles, and SOUL content use one append-only artifact mechanism. |
| D-21 | Myelination and usage affect salience/navigation only, never truth confidence. |
| D-22 | Legacy data is migrated once; compatibility behavior does not remain in canonical runtime paths. |

---

## 4. Problem Statement

### 4.1 Fragmented semantic authority

Core Memory currently contains separate semantic decisions for bead typing,
bead-field repair, rationale extraction, claims, claim updates, association
judging, Dreamer research, goal discovery, storyline convergence, SOUL
proposal, promotion, retrieval planning, causal execution, and verification.
These paths do not share a single failure policy or authorship contract.

Consequences include:

- deterministic code assigning semantic defaults;
- one task failing open while another remains pending;
- semantic provenance varying by feature;
- unsupported derived meaning acquiring durable storage;
- feature behavior changing through unrelated configuration combinations.

### 4.2 Multiple storage authorities

Canonical and derived state is distributed across JSONL files, mutable JSON
indexes, head caches, manifests, candidate queues, revision files, graph
stores, vector stores, and feature-specific event logs. Numerous modules read
or write the same index directly.

Consequences include:

- partial writes;
- projection drift;
- ambiguous recovery ordering;
- correctness depending on which cache was last updated;
- difficult transaction boundaries;
- expensive and unreliable replay;
- integration code reaching into persistence internals.

### 4.3 Observation and inference are not structurally separated

The current bead contract permits derived companion beads and validates source
linkage without proving that every label, title, summary, or causal statement
is entailed by the observed event. A structurally valid payload can therefore
introduce an unsupported goal or lesson.

### 4.4 Current truth is not uniquely resolved

Claims are stored with update records, but multiple resolvers can select
different current values when competing claims lack an explicit chain.
Selecting the latest survivor silently manufactures current truth from ordering
rather than semantic judgment.

### 4.5 Derived features operate as separate semantic engines

Dreamer, goal discovery, storyline convergence, SOUL synthesis, promotion, and
myelination have independent models, thresholds, queues, storage records, and
confidence rules. Much of their machinery performs the same abstract task:
construct an evidence set, interpret it, propose meaning, and govern whether
that meaning becomes current.

### 4.6 Retrieval is a collection of pathways

Generic recall, canonical search, causal recall, hybrid retrieval, context
recall, claim-first answering, reranking, source hydration, and quality gates
can use different relevance and fallback rules. A query may not receive every
available retrieval capability, and failure can yield a deterministic answer
instead of a semantic abstention.

### 4.7 Configuration changes invariants

Feature flags and environment variables can enable or disable fundamental truth
properties. Architectural invariants such as LLM semantic authorship,
fail-closed behavior, and explicit supersession must not depend on deployment
configuration.

---

## 5. Goals

### 5.1 Product goals

1. Make ObservationBeads faithful, evidence-cited notes of observed events.
2. Permit zero assertions without degrading or rejecting a bead.
3. Ensure all semantic conditional logic is performed by an attributed LLM.
4. Make unavailable or invalid semantic work retryable without inventing
   replacement meaning.
5. Establish one append-only transactional ledger for all canonical semantic
   state.
6. Provide one current-state resolver with unambiguous as-of behavior.
7. Preserve directional and bi-temporal relationship semantics natively.
8. Treat aggregate associations as evidence while preserving source
   independence, contradiction, and temporal distribution.
9. Consolidate all synthesized meaning into one Artifact system.
10. Preserve lossless promotion and reversible hot-context compression.
11. Provide one complete retrieval pipeline using every Core Memory retrieval
    capability.
12. Guarantee background task coverage with one job mechanism.
13. Retire JSONL and mutable JSON indexes without losing historical memory.
14. Reduce the number of runtime states, flags, and code paths required to
    operate Core Memory.
15. Provide measurable semantic quality and operational reliability gates.

### 5.2 Engineering goals

1. Make invalid semantic states difficult or impossible to represent.
2. Make canonical writes atomic and idempotent.
3. Make every projection rebuildable from the ledger.
4. Make every semantic result traceable to evidence, model, prompt, schema, and
   revision.
5. Separate domain truth from indexes, caches, integrations, and renderings.
6. Keep local and hosted behavior contract-equivalent.
7. Enable incremental migration without maintaining a permanent dual runtime.

### 5.3 Primary users and outcomes

| User/actor | Required outcome |
|---|---|
| End user | The agent remembers what actually happened, distinguishes current from historical state, and admits uncertainty. |
| Host agent | Every observed event is durably captured, semantically annotated, and retrievable without the host implementing memory internals. |
| Integrator | One stable event and retrieval API works across local and hosted deployments. |
| Operator | Pending, failed, ambiguous, stale, and complete states are distinguishable and recoverable. |
| Reviewer/auditor | Every meaning-bearing output is traceable to evidence, LLM receipt, and revision history. |
| Privacy administrator | Source content can be restricted, redacted, or lawfully erased without hidden copies in projections. |
| Developer | The domain kernel has one storage authority, one write path, one resolver, one Job protocol, and rebuildable projections. |

### 5.4 Core user stories

1. **As a user**, when I change a preference, I want Core Memory to preserve
   the old preference historically and resolve the new preference only through
   an explicit evidence-backed revision.
2. **As a user**, when evidence conflicts, I want the agent to say the state is
   ambiguous or contested instead of arbitrarily selecting the latest record.
3. **As a user**, I want routine conversation captured faithfully without the
   engine inventing goals, lessons, or claims.
4. **As an agent**, I want important memories expanded in my hot context and
   less important memories represented compactly without losing access to the
   full record.
5. **As an agent**, when a compact memory becomes relevant, I want to unpack it
   and hydrate its original source evidence.
6. **As an operator**, when the LLM provider is down, I want capture to continue,
   semantic Jobs to retry, and no deterministic meaning to be substituted.
7. **As an auditor**, I want to reconstruct what Core Memory believed at a
   particular valid time and what it knew at a particular system time.
8. **As an integrator**, I want semantic search, causal expansion, and source
   hydration available through one retrieval API rather than separate paths.
9. **As a maintainer**, I want to delete and rebuild search, graph, hot-context,
   or SOUL projections without losing canonical memory.
10. **As a privacy administrator**, I want all canonical and projected copies of
    restricted source content discoverable and governed through one ledger.

---

## 6. Non-Goals

1. This PRD does not require assertions on every bead.
2. It does not claim LLM output is infallible or externally verified truth.
3. It does not require synchronous semantic completion before acknowledging
   durable SourceEvent capture.
4. It does not require every query to use maximum graph depth, maximum source
   hydration, or the most expensive model.
5. It does not remove semantic search, causal expansion, source hydration,
   promotion, Dreamer, goals, storylines, SOUL, or myelination-derived salience.
6. It does not make vector or graph stores canonical databases.
7. It does not preserve JSONL as a live write format or fallback authority.
8. It does not support silent import of malformed legacy semantics into current
   truth.
9. It does not allow a user-facing integration to bypass ledger validation.
10. It does not define a new foundation model or train a custom model.
11. It does not require one physical worker process; it requires one worker
    protocol and one jobs table.
12. It does not treat deterministic execution of an LLM-authored plan as
    deterministic semantic authorship.

---

## 7. Terminology

| Term | Definition |
|---|---|
| **SourceEvent** | Immutable bounded evidence describing an observed user, agent, tool, document, or operational-system event. |
| **ObservationBead** | Exactly one LLM-authored annotation of a SourceEvent. |
| **Assertion** | Optional proposition supported by one or more beads. |
| **ClaimAssertion** | Subject-slot-value proposition about state, preference, identity, policy, commitment, or another typed claim. |
| **AssociationAssertion** | Directed semantic relationship between two canonical objects. |
| **Revision** | Append-only semantic decision affecting the current interpretation of an assertion or artifact. |
| **Artifact** | Evidence-bound synthesis derived from multiple observations or assertions. |
| **EvidenceSet** | Structured supporting, contradicting, and contextual evidence supplied to or returned by an LLM task. |
| **Valid time** | Time interval during which a proposition is asserted to hold in the represented world. |
| **System time** | Time interval during which Core Memory records a semantic record as part of its known ledger state. |
| **Current truth** | The uniquely resolved current terminal of an explicit revision chain, or an ambiguous/contested state when no unique terminal exists. |
| **Promotion** | LLM-authored relative-value selection controlling expanded versus compressed representation in hot context. |
| **Hot context** | Token-bounded memory supplied directly to the active agent. |
| **Archive** | Full canonical bead and evidence storage in SQLite/PostgreSQL. |
| **Unpacking** | Resolving a compressed hot-context bead reference to its complete archived record and source evidence. |
| **Projection** | Rebuildable derived representation such as a vector index, graph adjacency view, current-state table, SOUL rendering, or hot-context view. |
| **Semantic operation** | An LLM-backed task executed through the single semantic runtime. |
| **Mechanical operation** | Deterministic structure, persistence, search execution, traversal, scheduling, validation, or rendering that does not author meaning. |
| **Job** | Durable operational obligation stored in the single jobs table. |

---

## 8. Governing Invariants

### INV-01 — One event, one bead

Each accepted SourceEvent must have exactly one canonical ObservationBead
lineage and exactly one current immutable bead version. A retry may produce
multiple task attempts but must not create multiple lineages. A later grounded
correction appends a replacement version plus a Revision; it does not edit the
original version.

### INV-02 — Observation grounding

Every meaning-bearing bead field must be supported by cited SourceEvent spans,
structured tool results, document coordinates, or external record fields.

### INV-03 — Optional assertions

The valid cardinality of assertions, associations, and revisions in an
AnnotationBundle is zero or more. No assertion field is required for bead
validity.

### INV-04 — LLM semantic authority

Labels, titles, summaries, claims, claim-update decisions, semantic relations,
valid-time interpretations, artifact content, promotion ranking, retrieval
relevance, conflict interpretation, and final synthesis are LLM-authored.

### INV-05 — No deterministic semantic fallback

If a semantic operation fails, times out, or returns invalid output, Core Memory
records the failure and retries. It does not synthesize replacement meaning.

### INV-06 — Append-only canonical semantics

SourceEvents, beads, assertions, revisions, artifacts, context-view revisions,
and semantic task receipts are immutable after insertion.

### INV-07 — Explicit revision

No assertion or artifact changes current meaning without a Revision record
identifying the target, operation, evidence, and authorship receipt.

### INV-08 — One current-state resolver

All APIs, retrieval, promotion, artifacts, and projections use the same resolver
implementation and resolution rules.

### INV-09 — Ambiguity is preserved

Multiple incompatible terminal assertions without an explicit resolution must
produce `ambiguous`, never an implicit latest-wins answer.

### INV-10 — Bi-temporal truth

Canonical assertions and revisions preserve both valid time and system time.
As-of queries specify which axis or axes they constrain.

### INV-11 — Canonical direction

Every association is stored in one forward canonical direction. Reverse
traversal uses a deterministic inverse mapping and never requires a duplicate
semantic assertion.

### INV-12 — One ledger

SQLite/PostgreSQL is the only canonical live store. All other stores are
projections or exports.

### INV-13 — Atomic obligations

A ledger write and the creation of every mandatory resulting Job occur in the
same database transaction.

### INV-14 — Projection dispensability

Deleting any vector, graph, cache, hot-context, SOUL-rendering, or materialized
view store must not delete canonical truth. The projection must be rebuildable.

### INV-15 — Promotion is lossless

Promotion affects hot-context representation only. Full bead content always
remains available in the archive ledger.

### INV-16 — Salience is not truth

Recall usage, myelination, approval frequency, and retrieval reward may affect
navigation and ranking but must not change evidence, revision state, or factual
authority.

### INV-17 — Complete retrieval capability

The canonical retrieval pipeline must make semantic search, claim resolution,
causal expansion, association expansion, and source hydration available to the
LLM planner for every eligible query.

### INV-18 — One job protocol

Every background task uses the same lease, retry, idempotency, dependency,
failure, and observability contract.

### INV-19 — Tenant isolation

Every canonical row and job is scoped to a tenant/workspace boundary. No
semantic operation receives or returns cross-tenant evidence.

### INV-20 — Legacy data never gains authority during migration

Migration preserves historical provenance and uncertainty. It must not upgrade
fallback or heuristic content into canonical LLM-authored truth.

---

## 9. Semantic Authority Boundary

### 9.1 LLM-owned decisions

The LLM must author or explicitly abstain from:

- primary observation label;
- bead title and summary;
- observation facets and semantic qualifiers;
- retrieval title and facts when applicable;
- whether any assertion is warranted;
- claim subject, slot, value, scope, and rationale;
- whether new evidence reaffirms, supersedes, retracts, contests, or resolves
  an existing assertion;
- association predicate, canonical source and target, qualifiers, and
  rationale;
- valid-time interval interpretation when expressed by evidence;
- evidence support versus contradiction roles;
- source-independence assessment when aggregation requires semantic judgment;
- artifact type, content, evidence interpretation, limitations, and revision
  triggers;
- relative bead value for hot-context promotion;
- query intent, semantic planning, relevance, sufficiency, and synthesis;
- whether to answer or abstain.

### 9.2 Deterministic responsibilities

Core Memory deterministically owns:

- IDs and tenant/workspace attachment;
- `recorded_at` and system-time intervals;
- source hashes and idempotency keys;
- schema and referential-integrity validation;
- evidence-reference existence checks;
- append transactions;
- job creation, leasing, retries, backoff, and reconciliation;
- token counting and budget packing after LLM ranking;
- vector search execution;
- lexical search execution where enabled;
- graph traversal and inverse-edge traversal;
- bi-temporal interval comparisons;
- current-state chain traversal after semantic revision decisions exist;
- source lookup and hydration execution;
- projection updates and rebuilds;
- rendering and export;
- encryption, authorization, retention mechanics, and audit logging;
- operational health metrics.

### 9.3 Prohibited deterministic behavior

Deterministic code must not:

- choose a semantic bead label from content keywords;
- generate or repair a title, summary, rationale, goal, lesson, or storyline;
- default an unknown bead label to `context` or another meaningful type;
- default a missing claim-update decision to `reaffirm`;
- default a missing association predicate to `associated_with`;
- infer causal direction from chronology alone;
- turn repeated tokens into a latent goal;
- turn edge count, recall frequency, or myelination into truth confidence;
- select the latest incompatible claim as current without an explicit revision;
- promote beads using a deterministic semantic score;
- generate a fallback retrieval answer;
- auto-write deterministic text into SOUL or another artifact;
- interpret missing semantic work as successful completion.

### 9.4 Allowed deterministic validation outcome

Deterministic validation may reject or quarantine an LLM result. It may report:

- missing required structural field;
- invalid enum value;
- unknown referenced ID;
- missing evidence reference;
- out-of-range timestamp;
- invalid interval;
- duplicate idempotency key;
- tenant mismatch;
- cycle or broken replacement reference;
- output not conforming to the requested schema.

Validation may not silently replace the invalid semantic value.

---

## 10. Target Architecture

```text
Host / Adapter
    |
    v
SourceEvent API
    |
    +-- atomic insert -------------------------------+
    |                                                |
    +-- annotate_event Job                           |
                                                     v
                                          Single SQL Ledger
                                                     |
                         +---------------------------+------------------+
                         |                           |                  |
                         v                           v                  v
                Semantic Operation             Job Worker        Projection Runner
                     Runtime                        |                  |
                         |                          |                  +-- vector index
                         |                          |                  +-- graph view
                         v                          |                  +-- current state
                AnnotationBundle                   |                  +-- hot context
                         |                          |                  +-- SOUL render
                         +-------- atomic append ---+

Query API
    |
    v
LLM Query Plan
    |
    +-- semantic search
    +-- claim resolution
    +-- causal/association expansion
    +-- source hydration
    |
    v
LLM Evidence Judgment and Synthesis
    |
    v
Citation Verification -> Answer or Abstention
```

### 10.1 Core components

| Component | Responsibility | Must not do |
|---|---|---|
| Ledger | Transactional canonical storage | Interpret meaning |
| Semantic Operation Runtime | Execute all LLM-authored work | Mutate canonical records directly |
| Job Store/Worker | Guarantee execution and retries | Create semantic fallback output |
| Resolver | Traverse explicit revision state | Infer missing revisions |
| Projector | Build rebuildable views and indexes | Become a truth authority |
| Retrieval Pipeline | Orchestrate complete evidence acquisition and LLM judgment | Bypass resolver or citation checks |
| Context Assembler | Apply LLM promotion ranking to a token budget | Delete or semantically upgrade beads |
| Artifact Service | Append proposals and revisions | Maintain feature-specific truth stores |
| Integration Adapters | Translate host events and responses | Reach into ledger internals |

### 10.2 Core service ports

The domain kernel should depend on narrow interfaces:

```text
Ledger
  append_source_event(...)
  append_annotation_bundle(...)
  append_artifact(...)
  append_revision(...)
  read_object(...)
  query_ledger(...)

SemanticRuntime
  run(operation_request) -> operation_result

JobStore
  enqueue(...)
  lease(...)
  succeed(...)
  retry(...)
  fail(...)

Projection
  apply(ledger_position)
  rebuild(...)

CandidateCollector
  collect(query_plan) -> candidate_refs

SourceHydrator
  hydrate(source_refs) -> hydrated_evidence
```

SQLite, PostgreSQL, vector, graph, hosted semantic, and local semantic adapters
implement these ports without changing the domain contract.

---

## 11. Canonical Domain Model

### 11.1 Common ledger envelope

All canonical semantic records share:

```yaml
id: string
tenant_id: string
workspace_id: string | null
record_kind: enum
schema_version: string
system_from: RFC3339 timestamp
idempotency_key: string
created_by: actor reference
metadata: object
```

Requirements:

1. `id` is globally unique and opaque.
2. `tenant_id` is required.
3. `workspace_id` is required for hosted multi-workspace contexts and nullable
   only where the deployment contract permits a tenant-global record.
4. `system_from` is assigned by the ledger transaction.
5. Canonical rows are immutable. Effective `system_to` is computed by
   revision/current-state projections from the timestamp of the applicable
   append-only revision; it is not written back to the original row.
6. `idempotency_key` has a tenant-scoped unique constraint.
7. Arbitrary `metadata` cannot carry unvalidated semantic fields.

### 11.2 SourceEvent

SourceEvent is the lossless observation envelope.

```yaml
SourceEvent:
  id: string
  tenant_id: string
  workspace_id: string | null
  session_id: string
  turn_id: string | null
  transaction_id: string | null
  trace_id: string | null
  event_kind: conversation | agent_action | tool_call | tool_result |
              document | operational_event | external_evidence | human_edit
  actor_refs: [ActorRef]
  observed_at: RFC3339 timestamp | null
  recorded_at: RFC3339 timestamp
  payload: encrypted structured object or content reference
  content_hash: string
  source_refs: [SourceRef]
  parent_event_ids: [string]
  sequence: integer | null
  idempotency_key: string
  redaction_state: none | redacted | restricted
```

Functional requirements:

- **FR-EVT-01:** Event capture must preserve the original ordered content or an
  immutable pointer to it.
- **FR-EVT-02:** Tool inputs and outputs must remain distinguishable.
- **FR-EVT-03:** User, agent, tool, document, and system actors must remain
  distinguishable.
- **FR-EVT-04:** Event capture must not require semantic task availability.
- **FR-EVT-05:** Replaying the same idempotency key must return the original
  SourceEvent and must not enqueue duplicate mandatory jobs.
- **FR-EVT-06:** Every accepted event must atomically create an
  `annotate_event` job.
- **FR-EVT-07:** Source payload access must respect tenant, workspace, and
  redaction policy.
- **FR-EVT-08:** Unsupported event kinds must be rejected at the adapter
  boundary rather than coerced into a generic semantic kind.

### 11.3 EvidenceRef and source spans

Every meaning-bearing output references evidence using a shared structure:

```yaml
EvidenceRef:
  source_event_id: string
  source_kind: message_span | tool_field | document_span | record_field |
               image_region | audio_segment | external_uri
  actor_id: string | null
  message_id: string | null
  start_offset: integer | null
  end_offset: integer | null
  json_pointer: string | null
  page: integer | null
  region: object | null
  start_time_ms: integer | null
  end_time_ms: integer | null
  content_hash: string
  excerpt: string | null
```

Requirements:

- References must resolve to the same tenant/workspace.
- Offsets and structured pointers must be mechanically validated.
- Excerpts are convenience copies; hashes and source coordinates control.
- Sensitive excerpts may be omitted while retaining a resolvable encrypted
  source reference.
- An LLM cannot cite an arbitrary URL or bead ID as evidence without a canonical
  SourceRef or EvidenceRef.

### 11.4 ObservationBead

ObservationBead is the constrained semantic note for one SourceEvent.

```yaml
ObservationBead:
  id: string
  lineage_id: string
  version: integer
  source_event_id: string
  primary_label: ObservationLabel
  facets: [ObservationFacet]
  title: string
  summary: [string]
  retrieval_title: string | null
  retrieval_facts: [string]
  entities: [EntityMention]
  topics: [string]
  evidence_refs: [EvidenceRef]
  observed_at: RFC3339 timestamp | null
  valid_from: RFC3339 timestamp | null
  valid_to: RFC3339 timestamp | null
  authored_by_task_id: string
  verification_task_id: string | null
  retrieval_eligible: boolean
  semantic_limitations: [string]
```

Required fields are:

- `id`;
- `lineage_id` and positive `version`;
- `source_event_id`;
- `primary_label`;
- `title`;
- at least one `summary` item;
- at least one EvidenceRef grounding the label/title/summary;
- `authored_by_task_id`;
- explicit `retrieval_eligible`.

Assertions, claims, associations, causal explanations, entities, topics,
retrieval facts, and valid-time values are not required.

Functional requirements:

- **FR-BEAD-01:** Exactly one logical bead lineage exists per SourceEvent and
  exactly one immutable version resolves as current.
- **FR-BEAD-02:** A bead may be thin and retrieval-ineligible.
- **FR-BEAD-03:** Every summary sentence must be attributable to one or more
  evidence references.
- **FR-BEAD-04:** A bead must not cite another bead as the sole observation
  source.
- **FR-BEAD-05:** Derived synthesis is represented as an Artifact, never a
  second companion bead for the same event.
- **FR-BEAD-06:** The LLM must return the least committal faithful label when the
  event does not support a richer category.
- **FR-BEAD-07:** Invalid semantic output leaves the SourceEvent pending for
  retry; no placeholder semantic bead is created.
- **FR-BEAD-08:** Deterministic validation may reject an unsupported evidence
  coordinate but may not rewrite the summary.
- **FR-BEAD-09:** Re-authoring or correcting a bead appends a new version in the
  same lineage plus a Revision that supersedes the prior version.
- **FR-BEAD-10:** Exact-evidence audit may request a specific bead version;
  ordinary retrieval resolves the current version through the one resolver.

### 11.5 AnnotationBundle

The canonical per-event semantic result is one bundle:

```yaml
AnnotationBundle:
  contract: core-memory.annotation-bundle.v1
  source_event_id: string
  bead: ObservationBeadDraft
  assertions: [AssertionDraft]          # optional, may be []
  associations: [AssociationDraft]      # optional, may be []
  revisions: [RevisionDraft]            # optional, may be []
  reviewed_prior_objects: [ReviewResult]
  limitations: [string]
  task_id: string
```

Requirements:

- `bead` is required.
- All collections other than `bead` may be empty.
- Absence must be represented as an empty collection, not fabricated content.
- Bundle application is one database transaction.
- If any semantic row is invalid, policy determines either:
  - reject the entire bundle and retry; or
  - commit the valid bead and leave invalid optional rows as explicit pending
    child tasks.
- The initial release must choose one policy globally; it may not vary by
  adapter.
- Recommended v1 policy: commit only when the bead is valid; quarantine invalid
  optional rows with retryable child jobs and a truthful partial-completion
  receipt.

### 11.6 Assertion base

```yaml
Assertion:
  id: string
  assertion_kind: claim | association
  evidence_refs: [EvidenceRef]
  supporting_bead_ids: [string]
  valid_from: RFC3339 timestamp | null
  valid_to: RFC3339 timestamp | null
  recorded_at: RFC3339 timestamp
  epistemic_origin: observed_statement | extracted_source | inferred_synthesis |
                    human_endorsed | externally_verified
  authored_by_task_id: string
  semantic_confidence: number | null
  limitations: [string]
```

`semantic_confidence` is optional LLM metadata. It is not used by the resolver
to manufacture current truth and cannot be increased deterministically through
recall or usage.

### 11.7 ClaimAssertion

```yaml
ClaimAssertion:
  assertion_kind: claim
  claim_kind: preference | identity | policy | commitment | state | fact |
              goal_state | custom
  subject_ref: ObjectRef
  slot: string
  value: typed JSON value
  context_scope: string | null
  reason: string
  evidence_refs: [EvidenceRef]
  supporting_bead_ids: [string]
  valid_from: timestamp | null
  valid_to: timestamp | null
  authored_by_task_id: string
```

Requirements:

- A ClaimAssertion is optional.
- Subject, slot, and value are required only when a claim exists.
- A claim about what a user said must not be represented as independently
  verified world fact unless external verification evidence exists.
- A new incompatible claim in the same scoped slot must be accompanied by a
  Revision or leave the slot ambiguous.
- A single initial claim may become current without a revision when it is the
  only active terminal in its scoped slot.
- Claim identity and deduplication must not rely only on normalized text.

### 11.8 AssociationAssertion

```yaml
AssociationAssertion:
  assertion_kind: association
  source_ref: ObjectRef
  predicate: AssociationPredicate
  target_ref: ObjectRef
  qualifier: object
  rationale: string
  truth_basis: observed | evidence_supported | inferred | human_endorsed
  evidence_refs: [EvidenceRef]
  supporting_bead_ids: [string]
  contradicting_bead_ids: [string]
  valid_from: timestamp | null
  valid_to: timestamp | null
  recorded_at: timestamp
  authored_by_task_id: string
  candidate_run_id: string | null
```

#### 11.8.1 Canonical direction

Associations are stored as one directed triple:

```text
source_ref --predicate--> target_ref
```

The predicate registry defines an inverse for query traversal where an inverse
exists. Examples:

| Stored predicate | Computed reverse presentation |
|---|---|
| `causes` | `caused_by` |
| `enables` | `enabled_by` |
| `blocks` | `blocked_by` |
| `supports` | `supported_by` |
| `supersedes` | `superseded_by` |
| `part_of` | `contains` |
| `refines` | `refined_by` |

Only the stored forward assertion is canonical. The reverse presentation is a
query projection, not a second assertion.

#### 11.8.2 Bi-temporal semantics

Every association supports:

- **valid time:** `valid_from` and `valid_to`, describing when the relationship
  holds in the represented world;
- **system time:** `recorded_at` plus append-only revision history, describing
  when Core Memory knew or treated the relationship as current.

Rules:

1. `recorded_at` is always assigned by the ledger.
2. The LLM may extract `valid_from`/`valid_to` only from evidence.
3. Unknown valid time remains null and must not default to `recorded_at`.
4. An open interval uses null for the unknown/open bound.
5. Corrections append a replacement assertion plus a Revision.
6. As-of queries may request valid time, system time, or both.
7. Interval comparisons and filtering are deterministic.
8. Semantic interpretation of phrases such as “starting next quarter” is LLM
   work grounded in the SourceEvent and its known observation time.

#### 11.8.3 Directional validity

Direction must be authored explicitly by the LLM as `source_ref`, `predicate`,
and `target_ref`. Chronological order, candidate generation order, or graph
storage order must not choose semantic direction.

### 11.9 Revision

```yaml
Revision:
  id: string
  target_kind: bead | assertion | artifact | context_view
  target_id: string
  operation: reaffirm | supersede | retract | contest | resolve |
             accept | reject | endorse
  replacement_id: string | null
  related_revision_ids: [string]
  reason: string
  evidence_refs: [EvidenceRef]
  trigger_bead_ids: [string]
  valid_from: timestamp | null
  valid_to: timestamp | null
  recorded_at: timestamp
  authored_by_task_id: string
```

Requirements:

- **FR-REV-01:** Revisions are append-only.
- **FR-REV-02:** `supersede` requires a valid `replacement_id` of a compatible
  semantic kind.
- **FR-REV-03:** `retract` must not invent a replacement.
- **FR-REV-04:** `contest` preserves all contested terminals.
- **FR-REV-05:** `resolve` identifies which contested terminal or replacement
  becomes current.
- **FR-REV-06:** A missing or invalid operation is rejected, never defaulted.
- **FR-REV-07:** Revision ordering is assigned transactionally per resolution
  scope.
- **FR-REV-08:** Cycles in supersession/replacement chains are invalid.
- **FR-REV-09:** The original target row remains immutable.
- **FR-REV-10:** Current-state flags are computed projections.
- **FR-REV-11:** `accept`, `reject`, and `endorse` apply only to compatible
  reviewable targets and produce current review-state projections without
  mutating the target row.

### 11.10 EvidenceSet

Evidence aggregation uses one shared structure:

```yaml
EvidenceSet:
  supporting_assertion_ids: [string]
  contradicting_assertion_ids: [string]
  contextual_assertion_ids: [string]
  supporting_bead_ids: [string]
  contradicting_bead_ids: [string]
  source_event_ids: [string]
  independence_groups: [EvidenceIndependenceGroup]
  temporal_distribution:
    earliest_valid_time: timestamp | null
    latest_valid_time: timestamp | null
    session_count: integer
  missing_expected_evidence: [string]
  alternative_explanations: [string]
  limitations: [string]
```

An evidence set is not itself proof of a conclusion. It is the structured input
to an LLM assessment or artifact proposal.

Repeated associations derived from the same source event belong to the same
independence group. Deterministic code may group identical source IDs; semantic
judgment is required when independence depends on meaning rather than identity.

### 11.11 Artifact

```yaml
Artifact:
  id: string
  artifact_kind: goal | storyline | dreamer_finding | lesson | principle |
                 identity | value | tension | worldline | future_projection |
                 custom
  title: string
  content: structured object
  evidence_set: EvidenceSet
  epistemic_status: proposed | inferred | endorsed | verified
  initial_review_state: pending | accepted
  valid_from: timestamp | null
  valid_to: timestamp | null
  revision_triggers: [string]
  authored_by_task_id: string
  verification_task_id: string | null
  recorded_at: timestamp
```

Requirements:

- Artifacts are append-only.
- A mutation creates a new Artifact plus a Revision superseding the previous
  artifact.
- Current review state is computed from the initial state plus appended
  `accept`, `reject`, or `endorse` Revisions. Review does not update the
  Artifact row.
- `accepted` does not mean externally verified; epistemic status remains
  separate.
- Deterministic candidate generation may create a Job or neutral candidate
  reference but must not populate meaning-bearing artifact content.
- Goal, storyline, Dreamer, and SOUL services must use this same persistence and
  revision mechanism.

### 11.12 ContextViewRevision

```yaml
ContextViewRevision:
  id: string
  session_id: string
  previous_revision_id: string | null
  token_budget: integer
  expanded_bead_ids: [string]
  compressed_bead_ids: [string]
  pinned_artifact_ids: [string]
  ranking: [ContextRankItem]
  rationale: string
  authored_by_task_id: string
  recorded_at: timestamp
```

`ContextRankItem` contains:

```yaml
bead_id: string
relative_rank: integer
representation: expanded | compressed
reason: string
```

Requirements:

- Promotion is LLM-authored relative ranking.
- Deterministic code may pack ranked items into the token budget without
  changing rank or semantic rationale.
- Compressed beads remain full rows in the archive.
- The minimum compressed representation contains bead ID, primary label/type,
  and association references.
- Every compressed bead must support unpacking through one archive lookup.
- Context-view revisions are append-only.
- A current-context projection may point to the latest revision.

### 11.13 SemanticTaskReceipt

```yaml
SemanticTaskReceipt:
  id: string
  operation_kind: string
  task_id: string
  attempt: integer
  status: succeeded | invalid | unavailable | timed_out | failed
  input_refs: [ObjectRef]
  evidence_refs: [EvidenceRef]
  model_provider: string
  model_id: string
  model_tier: string
  prompt_version: string
  rubric_version: string
  output_schema: string
  input_hash: string
  output_hash: string
  started_at: timestamp
  completed_at: timestamp | null
  latency_ms: integer | null
  token_usage: object
  validation_errors: [string]
  limitations: [string]
```

Receipts are immutable and never reused across attempts. A stable task ID and
input hash connect retries.

### 11.14 Job

Jobs are operational and may update lease/state columns. They are not canonical
semantic records.

```yaml
Job:
  id: string
  tenant_id: string
  workspace_id: string | null
  kind: string
  subject_kind: string
  subject_id: string
  input_version: string
  payload: structured object
  dependency_ids: [string]
  state: pending | leased | retryable | succeeded | terminal_failure | cancelled
  priority: integer
  attempt_count: integer
  max_attempts: integer | null
  next_attempt_at: timestamp
  lease_owner: string | null
  lease_expires_at: timestamp | null
  last_error_code: string | null
  last_error_detail: string | null
  created_at: timestamp
  updated_at: timestamp
```

Unique constraint:

```text
(tenant_id, workspace_id, kind, subject_id, input_version)
```

This makes event-derived obligations idempotent and reconciliation-safe.

---

## 12. Canonical SQL Ledger

### 12.1 Storage posture

SQLite and PostgreSQL must implement the same logical ledger contract.

- SQLite is the default local embedded store.
- PostgreSQL is the hosted and multi-worker store.
- JSONL is not a live canonical store, write-ahead substitute, or recovery
  fallback.
- JSON or JSONL may be offered only as explicit export output, never as a
  continuously synchronized authority.
- Vector and graph systems contain only projections keyed to canonical IDs and
  ledger positions.

### 12.2 Logical tables

The initial schema must include:

```text
source_events
observation_beads
assertions
claim_assertions
association_assertions
revisions
artifacts
artifact_evidence_links
context_view_revisions
semantic_task_receipts
jobs
projection_watermarks
projection_failures
```

Typed child tables or JSON payload columns may be used, but referential and
tenant integrity must be enforced by the database wherever possible.

### 12.3 Transaction boundaries

Required atomic transactions:

1. **Event capture:** SourceEvent + mandatory `annotate_event` Job.
2. **Annotation commit:** ObservationBead + valid optional assertions + valid
   revisions + downstream Jobs + semantic receipt link.
3. **Artifact commit:** Artifact + evidence links + downstream projection Jobs.
4. **Revision commit:** Revision + resolver/projection Jobs.
5. **Context assembly:** ContextViewRevision + current-view projection Job.

Projection execution does not share the canonical write transaction, but the
obligation to project must be created inside it.

### 12.4 Ledger ordering

Each canonical append receives a monotonically increasing tenant-scoped ledger
position. Projectors advance watermarks against this position.

Requirements:

- ledger position assignment is transactional;
- replay order is stable;
- projectors may be at different watermarks;
- API receipts expose semantic and projection completion separately;
- retrieval may choose whether a stale projection is acceptable or direct
  ledger fallback is required, but it must report degradation truthfully.

### 12.5 Immutability enforcement

Canonical tables should deny UPDATE and DELETE to ordinary runtime roles.

Permitted exceptions:

- cryptographic erasure or legally required deletion through a privileged,
  audited privacy workflow;
- correction of mechanically corrupt data through an explicit maintenance
  migration with audit receipt;
- operational Job state updates;
- rebuildable projection-table mutation.

Semantic correction always uses append-only replacement and Revision records.

### 12.6 Indexing requirements

Canonical SQL indexes must support:

- SourceEvent lookup by tenant/session/turn;
- bead lookup by SourceEvent and observed time;
- assertion lookup by subject/slot/context scope;
- association traversal by source and target;
- valid-time interval queries;
- revision lookup by target and replacement;
- artifact lookup by kind, review state, and evidence IDs;
- semantic receipt lookup by task and input hash;
- job leasing by state, priority, and next attempt;
- projection replay by ledger position.

### 12.7 Hosted tenant isolation

PostgreSQL must enforce tenant/workspace isolation through row-level security or
an equivalent database-enforced mechanism. Application-only filtering is not
sufficient.

SQLite deployments must bind one database to one tenant boundary unless an
explicit multi-tenant SQLite mode provides equivalent enforcement.

---

## 13. Reduced Semantic Vocabulary

### 13.1 Design principle

The ontology must distinguish semantic meaning from source format, lifecycle,
severity, and epistemic status. The LLM should make a small number of
orthogonal decisions rather than one choice from a mixed vocabulary.

### 13.2 Observation labels v1

The proposed closed primary-label vocabulary is:

| Label | Use when the event primarily documents |
|---|---|
| `statement` | A user or agent states context, preference, fact, belief, constraint, or explanation. |
| `request` | A user or agent asks for work, information, permission, or a future action. |
| `goal` | A goal is explicitly expressed, adopted, changed, advanced, blocked, completed, or abandoned. |
| `decision` | A choice, commitment, policy selection, or explicit reversal is observed. |
| `action` | A user, agent, tool, or system performs a meaningful action. |
| `result` | An action or process yields an outcome, state change, success, failure, or partial result. |
| `evidence` | The event primarily introduces evidence used to support or challenge another proposition. |
| `reflection` | The user or agent explicitly evaluates, interprets, learns from, or reconsiders prior events. |

The final vocabulary must be validated through the evaluation program before
schema freeze, but it must remain small and mutually understandable.

### 13.3 Observation facets

Facets express orthogonal details without multiplying primary labels:

```yaml
ObservationFacet:
  kind: blocked | incident | hypothesis | lesson | principle | precedent |
        correction | reversal | checkpoint | completion | failure |
        preference | identity | commitment | state_change | custom
  value: typed value
  evidence_refs: [EvidenceRef]
```

Rules:

- facets are LLM-authored and evidence-grounded;
- facets do not replace the primary label;
- operational source type is not a facet;
- unknown facet kinds are rejected from canonical writes unless schema version
  explicitly permits a namespaced extension;
- extensions must not be silently normalized into a canonical facet.

### 13.4 Source-kind separation

The following current-style distinctions belong on SourceEvent rather than in
the semantic bead label:

- transcript;
- document reference;
- structured observation;
- tool call;
- operational event;
- session boundary;
- external evidence source.

A document may yield a `statement`, `evidence`, `result`, or another semantic
bead label. Source format and semantic meaning must not compete.

### 13.5 Lifecycle separation

The following belong in mechanical or revision state rather than primary bead
type:

- session start/end;
- archived/hot/cold representation;
- promoted/compressed;
- superseded/retracted/contested;
- candidate/accepted/rejected;
- pending semantic work.

### 13.6 Association predicates v1

The proposed canonical predicate set is:

| Predicate | Meaning |
|---|---|
| `supports` | Source provides evidence or rationale for target. |
| `contradicts` | Source is incompatible with or challenges target. |
| `causes` | Source is judged to produce or materially lead to target. |
| `enables` | Source makes target possible without being sufficient cause. |
| `blocks` | Source prevents or materially obstructs target. |
| `depends_on` | Source requires target or is conditional on target. |
| `resolves` | Source resolves the state, problem, tension, or goal represented by target. |
| `supersedes` | Source replaces target as the current semantic object. |
| `part_of` | Source is a component, episode, or member of target. |
| `similar_to` | Source and target share a relevant pattern without a stronger relation. |
| `refines` | Source narrows, clarifies, or adds precision to target. |

Qualifiers retain additional specificity:

```yaml
qualifier:
  subtype: string | null
  polarity: positive | negative | mixed | null
  modality: actual | possible | expected | counterfactual | null
  causal_role: trigger | condition | mechanism | consequence | null
  scope: string | null
```

Requirements:

- relation normalization must not change semantic meaning;
- inverse labels are presentation-level only;
- `similar_to` is not a fallback for missing judgment;
- no association is required when no supported predicate exists;
- predicate precision and direction must be evaluated independently.

### 13.7 Epistemic dimensions

The target schema separates:

1. **origin** — observed statement, source extraction, inference, human
   endorsement, external verification;
2. **current-state resolution** — current, ambiguous, contested, superseded,
   retracted, unknown;
3. **review state** — pending, accepted, rejected;
4. **salience** — retrieval/navigation usefulness;
5. **optional LLM confidence** — non-authoritative task metadata.

The C/B/A confidence ladder is not part of canonical current truth in this
design. Migration may preserve legacy values as provenance metadata, but new
runtime logic must use the separated dimensions above.

---

## 14. Single Semantic Operation Runtime

### 14.1 Requirement

All LLM-backed work must use one runtime contract. Feature-specific code may
provide a prompt, evidence packet, output schema, and model policy, but it may
not implement its own transport, retry, fallback, receipt, or authority logic.

### 14.2 Operation envelope

```yaml
SemanticOperationRequest:
  task_id: string
  operation_kind: annotate_event | review_associations | resolve_claims |
                  synthesize_artifact | revise_artifact | assemble_context |
                  plan_retrieval | judge_evidence | synthesize_answer |
                  verify_semantic_output
  tenant_id: string
  workspace_id: string | null
  input_refs: [ObjectRef]
  evidence_set: EvidenceSet | null
  bounded_context: structured object
  output_schema: string
  prompt_version: string
  rubric_version: string
  model_policy: ModelPolicy
  authority: semantic_author | semantic_reviewer | candidate_author
  idempotency_key: string
  max_attempts: integer | null
  deadline: timestamp | null
```

```yaml
SemanticOperationResult:
  task_id: string
  status: succeeded | invalid | unavailable | timed_out | failed
  structured_output: object | null
  limitations: [string]
  evidence_refs_used: [EvidenceRef]
  receipt: SemanticTaskReceipt
```

### 14.3 Multi-step reasoning

The runtime must permit one operation to perform multiple LLM reasoning steps
without exposing deterministic semantic branches.

For example, `annotate_event` may internally ask the LLM to:

1. identify observable interaction units;
2. select the primary label;
3. write title and summary;
4. verify every summary statement against source evidence;
5. decide whether assertions are warranted;
6. compare warranted claims with visible current claim state;
7. decide whether revisions are needed;
8. judge bounded association candidates;
9. return one AnnotationBundle.

The runtime may use one model call, an agent loop, or multiple model calls. The
domain contract observes one logical operation, stable task ID, evidence
boundary, and final structured result.

### 14.4 Conditional logic rule

Meaning-bearing conditional logic belongs to the LLM. Examples:

- “Does this event express a goal?” — LLM.
- “Does this claim contradict the current claim?” — LLM.
- “Is this candidate pair causally related?” — LLM.
- “Is source hydration sufficient to answer?” — LLM.
- “Which bead is more valuable in this active context?” — LLM.

Mechanical conditions remain deterministic:

- “Did the task return valid JSON?”
- “Does every EvidenceRef resolve?”
- “Has the lease expired?”
- “Does the token bundle exceed the budget?”
- “Has this projection processed ledger position N?”
- “Does the valid-time interval contain the requested timestamp?”

### 14.5 Retry policy

Semantic failure results in retry, not fallback.

Required retry behavior:

1. Unavailable provider: exponential backoff with jitter.
2. Timeout: retry with the same evidence boundary and idempotency key.
3. Invalid structured output: retry with validation errors supplied to the
   semantic runtime.
4. Context-length failure: deterministically reduce redundant evidence or use
   a larger-context model, without removing required source anchors.
5. Rate limit: honor provider retry timing.
6. Non-retryable authentication or policy failure: terminal operational failure
   requiring intervention.
7. Maximum attempts may be unlimited for mandatory observation annotation,
   subject to operator alerting and resource policy.
8. Every attempt receives a new immutable receipt.

### 14.6 No semantic fallback field

The canonical request/result schemas must not include `fallback_mode` for
semantic content. The only permitted degraded output is:

```text
status != succeeded
structured_output = null
job remains retryable or terminally failed with explicit operator action needed
```

### 14.7 Model routing

Model routing is a mechanical policy applied to an LLM operation, not a semantic
fallback.

Routing may consider:

- operation kind;
- evidence volume;
- uncertainty reported by prior attempts;
- artifact authority;
- latency/cost tier;
- provider availability;
- tenant policy;
- privacy/data residency.

Routing must not downgrade to deterministic semantics. If no compliant model is
available, the task remains unavailable.

### 14.8 Independent verification

Verification is required for:

- auto-applied SOUL identity/value changes;
- high-authority artifact acceptance;
- migration promotion of legacy semantic content;
- optionally, high-impact claim revision classes;
- release/evaluation samples.

Verification must use a separately attributed semantic operation and preferably
a different model or provider policy. Structural validation alone is not
semantic verification.

---

## 15. Canonical Write Pipeline

### 15.1 Public flow

```text
adapter observes event
  -> POST/observe or SDK observe(...)
  -> ledger appends SourceEvent
  -> same transaction creates annotate_event Job
  -> API returns durable capture receipt
  -> worker leases job
  -> semantic runtime authors AnnotationBundle
  -> Core Memory validates evidence and schema
  -> ledger appends bead and valid optional semantics atomically
  -> same transaction creates all downstream Jobs
  -> projections advance asynchronously
  -> semantic completion receipt becomes available
```

### 15.2 Capture receipt versus semantic receipt

The API must distinguish:

```yaml
capture_status: persisted | duplicate | rejected
semantic_status: pending | succeeded | partial | retrying | terminal_failure
projection_status: pending | current | degraded
```

`capture_status=persisted` does not imply that a bead has been authored.

### 15.3 Exactly-once semantic identity

At-least-once execution plus database uniqueness provides exactly-once semantic
identity:

- multiple task attempts are allowed;
- only one logical ObservationBead lineage may reference a SourceEvent;
- duplicate bundle application returns the canonical bead ID;
- optional child semantic rows use stable semantic idempotency keys;
- job retries cannot duplicate revisions or associations.

### 15.4 Bundle validation stages

1. Validate envelope and schema version.
2. Validate tenant/workspace ownership.
3. Validate bead required fields.
4. Validate evidence coordinate existence and hashes.
5. Validate closed vocabularies.
6. Validate temporal intervals.
7. Validate referenced current objects and candidate visibility.
8. Validate assertion-specific shapes.
9. Validate revision compatibility and acyclicity.
10. Validate semantic-task provenance.
11. Apply canonical IDs and system timestamps.
12. Commit in one transaction.

### 15.5 Partial optional-row handling

V1 behavior:

- A valid bead may commit even when an optional assertion or revision fails
  structural validation.
- Invalid optional rows are not silently dropped.
- Each invalid row creates a child `repair_optional_semantics` Job containing
  validation errors and the original task/output receipt.
- The semantic receipt reports `partial`.
- Retrieval cannot see the invalid row.
- The repair task must be LLM-authored and evidence-bound.

This policy preserves the observation while refusing unsupported optional
meaning.

### 15.6 Visibility

- SourceEvent is visible to audit immediately after capture.
- Bead becomes visible after successful annotation commit.
- Assertions become visible only after validation and commit.
- Current-state projections may lag but must expose their watermark.
- Semantic retrieval must not treat a pending SourceEvent as an authored bead.
- Operational interfaces may show pending events to authorized operators.

### 15.7 Flush/session boundaries

Session end and compaction must not fabricate beads for pending events.

A session receipt reports:

- captured SourceEvent count;
- authored bead count;
- pending annotation count;
- retrying/terminal semantic failures;
- projection watermarks;
- current hot-context revision;
- unresolved ambiguity count.

Flush may complete mechanical archival while accurately reporting incomplete
semantic work.

---

## 16. Assertion Optionality and Abstention

### 16.1 Product behavior

The LLM must be encouraged to return no assertions when the event does not
support a durable proposition.

Examples likely to yield zero assertions:

- greeting or acknowledgment;
- routine status request without a durable preference or commitment;
- tool invocation whose result has no durable relevance;
- repeated restatement already fully represented with no meaningful change;
- speculative content too weak to preserve as a proposition;
- conversation glue.

### 16.2 Valid zero-assertion result

```json
{
  "bead": {
    "primary_label": "statement",
    "title": "Brief greeting",
    "summary": ["The user greeted the agent."],
    "retrieval_eligible": false,
    "evidence_refs": ["..."]
  },
  "assertions": [],
  "associations": [],
  "revisions": []
}
```

This is a fully successful annotation.

### 16.3 Abstention metrics

The system must measure:

- assertion rate by event kind;
- unsupported assertion rate;
- gold-set precision and recall;
- abstention appropriateness;
- empty-output frequency by model/prompt;
- operator overrides of abstentions.

The goal is not to maximize assertion count. Precision and grounding control.

### 16.4 No richness gate

No quality gate may require:

- a claim;
- an association;
- a causal rationale;
- a retrieval fact;
- an entity;
- a promotion decision;
- an artifact proposal.

Quality gates evaluate faithfulness, not semantic density.

---

## 17. Association Coverage and Aggregate Evidence

### 17.1 Architecture

Association work has three stages:

1. deterministic high-recall candidate collection;
2. LLM relationship judgment;
3. append-only persistence of accepted assertions and review receipts.

Candidate collection is not graph truth.

### 17.2 Candidate collection

Candidate collectors may use:

- semantic/vector similarity;
- shared entity references;
- overlapping claim subjects or slots;
- temporal proximity;
- session/window context;
- shared source references;
- current goal/storyline membership;
- graph neighborhood;
- source type and scope;
- LLM-authored retrieval keys.

Collectors return neutral pairs and signals. They must not return an inferred
predicate or direction as authoritative input.

### 17.3 LLM association review

The LLM receives:

- full source and target beads;
- required source evidence excerpts;
- current assertion/revision context;
- neutral candidate signals;
- valid-time context;
- allowed predicate vocabulary;
- bounded visible object IDs.

For every candidate, it returns:

- accept or no supported association;
- source, predicate, target;
- rationale;
- truth basis;
- evidence references;
- valid-time interpretation where supported;
- limitations.

### 17.4 Rejected candidate persistence

Rejected/no-link judgments are stored in semantic task receipts or a dedicated
append-only review table, not as negative graph edges. This prevents repeated
review of identical candidate versions while avoiding false canonical
relationships.

### 17.5 Coverage definition

Coverage is operational, not a deterministic assertion of completeness.

Required measures:

- eligible beads with at least one completed association review batch;
- pending candidate batches;
- reviewed candidate count;
- accepted/rejected count;
- candidate-source diversity;
- age since last association review;
- semantic relevant-pair recall on evaluation sets;
- relationship precision;
- direction accuracy;
- evidence-reference accuracy;
- valid-time accuracy.

### 17.6 Coverage triggers

Association review Jobs are created when:

- a new bead is committed;
- a bead is unpacked and new evidence changes its candidate surface;
- an entity merge changes candidate neighborhoods;
- a claim/artifact revision changes relevant current state;
- a projection rebuild identifies unreviewed eligible versions;
- an operator explicitly requests re-review;
- a new association schema version requires migration/rejudgment.

### 17.7 Aggregate evidence

Accepted association assertions become evidence atoms. Aggregate evidence
assessment is an LLM artifact or claim-review operation over an EvidenceSet.

The operation must consider:

- independent SourceEvents;
- independent sessions and actors;
- duplicated evidence origins;
- supporting and contradicting assertions;
- valid-time distribution;
- source quality and directness;
- missing expected evidence;
- alternative explanations;
- revision history.

No fixed count of edges automatically proves a claim or artifact.

### 17.8 Independence

At minimum, deterministic grouping must prevent these from being counted as
independent:

- multiple edges from the same SourceEvent;
- multiple beads imported from the same immutable document span;
- repeated projection of the same claim;
- inverse presentations of one canonical association;
- restatements explicitly linked by reaffirm revisions.

The LLM assesses subtler dependence such as several sources repeating one
upstream assertion.

---

## 18. Revision Ledger and Current-State Resolver

### 18.1 Scope key

Claim resolution groups by:

```text
(tenant_id, workspace_id, subject_ref, slot, context_scope)
```

Association resolution groups by canonical association identity and scope.
Artifact resolution groups by artifact lineage or explicit logical key.

### 18.2 Terminal computation

The resolver deterministically traverses explicit Revision records.

For a scoped claim set:

1. Filter by requested valid time and system time.
2. Construct the revision graph.
3. Reject or flag cycles and missing references.
4. Mark superseded and retracted targets.
5. Preserve contested terminals.
6. Identify active terminal assertions.
7. Return state using the rules below.

### 18.3 Resolution states

| Condition | State |
|---|---|
| No visible assertion | `unknown` |
| All visible assertions retracted | `retracted` |
| Exactly one active terminal | `current` |
| Multiple semantically equivalent terminals with no reaffirm/dedup revision | `ambiguous` |
| Multiple incompatible terminals with no conflict revision | `ambiguous` |
| Explicit contest/conflict revision active | `contested` |
| Explicit resolution selects one terminal | `current` |
| Broken/cyclic revision chain | `invalid` |

The resolver must never choose a terminal because it was inserted last unless
an explicit revision establishes that semantic outcome.

### 18.4 Reaffirm

`reaffirm` records that new evidence supports an existing assertion without
creating a new current value. It requires:

- target assertion ID;
- new evidence refs/bead IDs;
- reason;
- LLM authorship receipt.

### 18.5 Supersede

`supersede` declares that a replacement becomes current relative to a target.
It requires semantic compatibility of scope and kind and an explicit
replacement ID.

### 18.6 Contest and resolve

When evidence supports incompatible active assertions, the LLM may append a
`contest` revision. The slot remains contested until a later `resolve`,
`supersede`, or `retract` revision addresses the terminals.

### 18.7 As-of API

Every current-state API must accept:

```yaml
valid_at: timestamp | null
known_at: timestamp | null
```

- `valid_at` constrains world/effective time.
- `known_at` constrains system/recorded time.
- absent values mean current query time on that axis.
- responses state both resolved timestamps and ledger watermark.

### 18.8 Resolver purity

The resolver is a pure domain function over ledger rows. It must not:

- call an LLM;
- inspect vector similarity;
- use insertion order as semantic evidence;
- use confidence or salience to break ties;
- write revisions;
- depend on a projection that cannot be rebuilt.

An LLM creates the semantic Revision. The resolver applies it mechanically.

---

## 19. Unified Artifact System

### 19.1 Purpose

Dreamer findings, latent goals, storylines, lessons, principles, identity/value
statements, tensions, worldlines, and future projections share one lifecycle:

```text
evidence changes
  -> artifact synthesis Job
  -> LLM authors Artifact proposal
  -> optional verification
  -> review/acceptance
  -> append-only artifact
  -> later evidence may produce replacement Artifact + Revision
```

### 19.2 Artifact kinds

Each artifact kind owns only:

- its output content schema;
- its prompt/rubric;
- its eligibility policy;
- its default review/verification requirement;
- its renderer or query projection.

All kinds share:

- EvidenceSet;
- semantic runtime;
- receipts;
- append-only persistence;
- Revision ledger;
- review state;
- current-state resolution;
- observability;
- job execution.

### 19.3 Dreamer

Dreamer becomes the policy/scheduler for longitudinal evidence synthesis, not a
deterministic semantic generator.

Dreamer may deterministically identify that:

- new evidence exists since watermark;
- multiple sessions qualify for review;
- an accepted artifact has reached a scheduled review date;
- an evidence neighborhood fits within a configured budget.

The LLM decides:

- whether a pattern exists;
- what the pattern means;
- whether it supports a goal, tension, identity/value hypothesis, storyline, or
  other artifact;
- confidence/limitations;
- alternative explanations;
- revision triggers.

### 19.4 Goals

Goal artifacts may originate from:

1. direct observation of an expressed/adopted goal;
2. LLM synthesis of a latent goal from multiple observations;
3. explicit human creation.

Directly observed goals retain links to their source bead and claims. Latent
goals remain proposed/inferred until accepted under policy.

Goal lifecycle transitions are append-only revisions or typed artifact-state
events. Deterministic token recurrence or overlap may schedule review but may
not author a goal title, statement, or transition.

### 19.5 Storylines and worldlines

Deterministic graph traversal may construct candidate episode sets. The LLM
authors:

- storyline title;
- narrative statement;
- relevance of episodes;
- tension or theme;
- temporal framing;
- alternative interpretations;
- revision triggers.

Worldline and storyline renderings are projections of accepted/current
artifacts plus canonical associations.

### 19.6 SOUL

SOUL identity, values, tensions, goals, and continuity statements are artifact
kinds.

SOUL.md and related files become renderings of accepted current artifacts. They
are not independent truth stores.

Requirements:

- no SOUL rendering is written from deterministic fallback content;
- user edits to a SOUL file are ingested as human-edit SourceEvents and processed
  through the ledger;
- artifact revision history remains available even when the rendered file shows
  only current state;
- auto-application requires the configured semantic verification policy;
- human endorsement is represented explicitly, not inferred from file presence.

### 19.7 Artifact review

Review modes:

- `human_required`;
- `semantic_verifier_required`;
- `auto_accept` for explicitly permitted low-risk artifact kinds;
- `proposal_only`.

Review policy is configuration, but it cannot permit deterministic semantic
authorship.

### 19.8 Artifact supersession

An accepted Artifact is never edited in place.

```text
A1 accepted
new evidence
A2 proposed
R1 supersedes A1 with A2
A2 accepted/current
```

The current-artifact projection exposes A1 as superseded. Historical queries
continue to return A1 for the appropriate system/valid time.

---

## 20. Promotion and Hot-Context Assembly

### 20.1 Product definition

Promotion is a Core Memory engine feature that asks an LLM to determine which
beads are relatively more valuable to the active context than others.

Promotion is:

- comparative;
- context-dependent;
- reversible;
- lossless;
- LLM-authored;
- independent of factual truth.

### 20.2 Representation tiers

| Tier | Hot-context representation | Archive state |
