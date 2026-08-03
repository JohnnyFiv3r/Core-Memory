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
|---|---|---|
| Expanded | Full or budgeted bead content, key evidence, current claims, useful associations | Full canonical record retained |
| Compressed | Bead ID, primary label/type, association references, and deterministic lookup key | Full canonical record retained |

No canonical bead is deleted or semantically downgraded by compression.

### 20.3 Promotion operation inputs

The LLM receives:

- active conversation objective and recent events;
- current expanded and compressed bead set;
- candidate bead summaries and associations;
- current goal/storyline/SOUL artifacts where authorized;
- retrieval/use signals labeled as salience only;
- token budget;
- pinned items and mandatory safety/context requirements;
- prior ContextViewRevision.

### 20.4 Promotion output

The LLM returns:

- relative ordering;
- expanded versus compressed recommendation;
- reason for each decision;
- beads that should be unpacked;
- artifacts that should remain pinned;
- uncertainty/limitations.

Deterministic code then:

- verifies IDs;
- calculates actual token cost;
- packs items in LLM rank order;
- preserves mandatory pinned records;
- emits a truthful overflow/degradation receipt if the requested expanded set
  cannot fit;
- appends ContextViewRevision.

### 20.5 Promotion triggers

Promotion/assembly Jobs must be created when:

- a new bead enters the active session;
- active context crosses a configurable token-pressure threshold;
- a current goal, storyline, or SOUL artifact changes;
- retrieval unpacks a bead and the active agent elects to retain it;
- the host requests context refresh;
- session start requires continuity hydration;
- a reconciliation sweep finds the active view behind its required ledger
  watermark.

### 20.6 Unpacking

Unpacking accepts a canonical bead ID and returns:

- full bead;
- source evidence permitted to the caller;
- current claims derived from or supported by the bead;
- accepted associations;
- relevant artifact memberships;
- revision/current-state status;
- archive/projection watermark.

Unpacking must not require semantic re-generation. It is deterministic archive
lookup plus optional source hydration.

### 20.7 Promotion evaluation

Evaluation must measure:

- critical-context retention;
- irrelevant expanded-content rate;
- task success with promoted hot context;
- successful unpack rate;
- token savings;
- semantic stability across repeated runs;
- sensitivity to changing active goals;
- regression versus full-context baseline;
- accidental correlation of promotion with truth/confidence.

---

## 21. One Complete Retrieval Pipeline

### 21.1 Product requirement

Every canonical query uses one retrieval pipeline. The pipeline makes all
capabilities available and uses LLM judgment for semantic branching,
relevance, sufficiency, and synthesis.

### 21.2 Pipeline stages

```text
1. Normalize authorization and mechanical request fields
2. LLM query planning
3. Candidate collection
4. Current-state resolution
5. Causal and association expansion
6. Source hydration
7. LLM evidence judgment
8. Optional iterative retrieval
9. LLM answer synthesis or abstention
10. Deterministic citation and tenant verification
```

### 21.3 LLM query plan

```yaml
RetrievalPlan:
  intent: string
  questions_to_resolve: [string]
  entity_refs: [ObjectRef]
  claim_slots: [ClaimSlotRef]
  valid_at: timestamp | null
  known_at: timestamp | null
  semantic_queries: [string]
  causal_targets: [ObjectRef]
  relationship_families: [AssociationPredicate]
  source_types: [string]
  hydration_requirements: [HydrationRequirement]
  effort:
    candidate_budget: integer
    graph_depth: integer
    source_budget: integer
    iteration_budget: integer
  answer_schema: string
  abstention_conditions: [string]
```

The LLM authors the plan. Core Memory validates it and executes allowed
operations.

### 21.4 Candidate collection

The single pipeline can union:

- vector/semantic results;
- lexical matches;
- current claim-slot matches;
- entity matches;
- goal/storyline/artifact matches;
- temporal matches;
- source metadata matches;
- hot-context beads;
- external-store candidates through authorized adapters.

Deterministic scores are candidate-generation signals only. They do not decide
the final answer or truth.

### 21.5 Current-state resolution

Relevant claim and artifact scopes are resolved through the one resolver before
answer synthesis. Ambiguous or contested state is passed explicitly to the LLM.

### 21.6 Causal and association expansion

The pipeline traverses accepted canonical associations using:

- LLM-requested relation families;
- deterministic direction/inverse mapping;
- valid/system-time filters;
- depth and candidate budgets;
- cycle control;
- provenance preservation.

The LLM judges whether expanded paths are relevant evidence. Traversal alone
does not establish causality.

### 21.7 Source hydration

The pipeline hydrates the evidence needed to assess candidate claims and paths.
Hydration includes full bead unpacking, adjacent turn context, tool results,
document spans, operational records, and authorized external sources.

### 21.8 Evidence judgment

The LLM receives a structured packet containing:

- candidate beads;
- current/ambiguous claim state;
- association paths;
- supporting and contradictory evidence;
- hydrated source excerpts;
- temporal constraints;
- source availability and limitations.

It returns:

- relevant evidence IDs;
- irrelevant candidate IDs;
- evidence roles;
- unresolved contradictions;
- sufficiency decision;
- follow-up retrieval requests within the remaining budget;
- limitations.

### 21.9 Iteration

If evidence is insufficient and budget remains, the LLM may request another
candidate/hydration round. The pipeline executes the request and returns the new
evidence to the same logical retrieval operation.

### 21.10 Synthesis and abstention

The final LLM result must:

- answer only what the selected evidence supports;
- distinguish observed, asserted, inferred, contested, and verified content;
- cite canonical evidence IDs/source refs;
- state temporal scope where material;
- surface ambiguity;
- abstain when evidence is insufficient.

No deterministic fallback answer is permitted.

### 21.11 Citation verification

Deterministic post-validation verifies:

- every citation exists;
- tenant/workspace access is valid;
- citation content was included in the evidence packet;
- claimed temporal scope is not mechanically incompatible with cited rows;
- no hidden or redacted source content is leaked;
- output matches the requested answer schema.

Invalid synthesis is retried or returned as a truthful retrieval failure, not
silently stripped into an unsupported answer.

### 21.12 Effort tiers

Effort tiers control budgets and model policy only. They must not create
separate truth semantics.

| Tier | Typical behavior |
|---|---|
| Low | Small semantic candidate set, shallow expansion, minimal hydration |
| Standard | Balanced semantic search, claim resolution, causal expansion, targeted hydration |
| High | Broader candidates, deeper paths, more source hydration, iterative evidence judgment |
| Frontier | High-authority synthesis, maximum approved evidence budget, stronger model/verification |

---

## 22. Source Hydration

### 22.1 Purpose

Beads are concise observations. Source hydration restores the original evidence
needed for high-fidelity judgment without forcing all raw content into hot
context or search indexes.

### 22.2 Hydration targets

- full SourceEvent payload;
- message spans and adjacent turns;
- tool input/output fields;
- document pages/spans;
- image regions;
- audio/video segments;
- operational system records;
- external connector content;
- full archived bead representation;
- historical revision records.

### 22.3 Hydration authorization

Hydration checks:

- tenant/workspace scope;
- actor/caller permissions;
- source connector grant;
- redaction policy;
- data residency;
- artifact or query purpose where policy requires it.

### 22.4 Hydration failure

Unavailable source content is reported as unavailable evidence. The retrieval
LLM decides whether remaining evidence is sufficient. Core Memory must not
replace missing source content with bead summary text while labeling it as full
source verification.

### 22.5 Hydration caching

Hydrated content may be cached as an encrypted projection with:

- source content hash;
- tenant scope;
- expiry;
- connector/version provenance;
- redaction state.

Cache invalidation is mechanical and must not alter canonical evidence refs.

---

## 23. One Jobs Table and Worker System

### 23.1 Architecture

All deferred work uses one `jobs` table and one worker protocol. Multiple
worker replicas may lease jobs concurrently from the same table.

### 23.2 Required job kinds

Initial job kinds:

| Job kind | Trigger | Result |
|---|---|---|
| `annotate_event` | SourceEvent append | ObservationBead and optional semantics |
| `repair_optional_semantics` | Partial bundle validation | Re-authored optional rows or explicit abstention |
| `project_search` | Canonical semantic append | Search/vector projection update |
| `project_graph` | Assertion/revision append | Graph projection update |
| `project_current_state` | Claim/artifact revision append | Current-state projection update |
| `review_associations` | Bead/evidence change | Association assertions and review receipts |
| `synthesize_artifacts` | Evidence watermark/cadence | Artifact proposals |
| `verify_artifact` | Policy requires verification | Verification receipt/decision |
| `assemble_context` | Turn/token/artifact trigger | ContextViewRevision |
| `render_soul` | Current SOUL artifact changes | SOUL file/view projection |
| `hydrate_source` | Retrieval or prefetch request | Hydrated source cache/receipt |
| `sync_external_projection` | Canonical change | Connector/graph/vector sync |
| `rebuild_projection` | Operator/recovery action | Projection rebuilt to watermark |
| `privacy_action` | Authorized privacy request | Audited redaction/erasure workflow |

### 23.3 Mandatory dependency graph

```text
SourceEvent
  -> annotate_event

ObservationBead/Assertion/Revision commit
  -> project_search
  -> project_graph
  -> project_current_state where applicable
  -> review_associations
  -> assemble_context if session-active

Association/evidence changes
  -> synthesize_artifacts when eligibility policy is met

Artifact/Revision changes
  -> project_current_state
  -> assemble_context where relevant
  -> render_soul for SOUL kinds
```

### 23.4 Eligibility scheduling

Deterministic scheduling may determine that a task is due based on:

- unprocessed ledger position;
- elapsed cadence;
- explicit dependency completion;
- active session status;
- token pressure;
- schema/model version change;
- operator request;
- failed projection watermark;
- artifact review due date.

It may not determine semantic content or whether a semantic candidate is true.

### 23.5 Leasing

Requirements:

- PostgreSQL uses row locking such as `FOR UPDATE SKIP LOCKED` or equivalent.
- SQLite uses a safe single-writer lease transaction.
- leases expire and may be reclaimed;
- workers heartbeat long operations;
- job handlers are idempotent;
- success is recorded only after canonical/result transaction commits;
- worker crash after commit but before acknowledgement resolves through
  idempotency.

### 23.6 Retry and terminal failure

- retryable state stores next attempt time and error class;
- exponential backoff includes jitter;
- mandatory semantic jobs do not silently disappear after a low retry ceiling;
- operator policy may pause or quarantine jobs;
- terminal failure requires a non-retryable reason and visible operator alert;
- manual retry appends an audit event;
- semantic task attempts remain independently receipted.

### 23.7 Reconciliation sweeps

A single reconciler compares canonical ledger state to required jobs and
projection watermarks.

It must detect:

- SourceEvents without annotation jobs;
- events without successful beads;
- committed semantic rows without required projection jobs;
- beads without required association-review versions;
- artifact changes without context/SOUL projection jobs;
- stale leases;
- projectors behind ledger watermarks;
- succeeded jobs whose claimed outputs do not exist.

The unique Job constraint makes missing-work creation idempotent.

### 23.8 Worker consolidation

There should be one worker executable/service and one handler registry. Handler
modules may be specialized, but they share:

- job schema;
- leasing;
- retry rules;
- logging;
- metrics;
- tenant context;
- semantic runtime access;
- graceful shutdown;
- health reporting.

### 23.9 Coverage SLO

For every mandatory job kind, the system reports:

- eligible subject count;
- job-created count;
- pending/leased/retryable/succeeded/failed count;
- oldest pending age;
- completion latency percentiles;
- reconciliation-created count;
- subjects missing any required job.

Release acceptance requires zero silently missing mandatory obligations in the
test and smoke environments.

---

## 24. Projections and Indexes

### 24.1 Projection principle

Projections improve access but never define truth. Every projection is keyed to
a ledger watermark and can be deleted and rebuilt.

### 24.2 Required projections

- current claim/artifact state;
- vector/semantic index;
- lexical index where retained;
- graph adjacency and traversal view;
- entity lookup;
- session/hot-context view;
- artifact-kind views;
- SOUL/GOALS/TENSIONS/WORLDLINES renderings;
- retrieval feedback and salience view;
- operator metrics/read models.

### 24.3 Projection watermark

```yaml
ProjectionWatermark:
  projection_name: string
  tenant_id: string
  workspace_id: string | null
  ledger_position: integer
  schema_version: string
  updated_at: timestamp
  state: current | rebuilding | degraded | failed
  last_error: string | null
```

### 24.4 Search projection

Search documents may include:

- bead title and summary;
- retrieval title/facts;
- accepted assertion text;
- artifact content where policy permits;
- source metadata;
- current-state status;
- temporal bounds;
- tenant/workspace filtering fields.

Search scores are relevance signals, not truth confidence.

### 24.5 Graph projection

Graph nodes and edges reference canonical IDs. The projection stores:

- accepted AssociationAssertions;
- computed inverse traversal metadata;
- valid/system-time filters;
- revision/current-state visibility;
- provenance pointers.

Graph backend loss must be recoverable from SQL without semantic re-authoring.

### 24.6 Hot-context projection

The active context view materializes the latest ContextViewRevision, expanded
bead content, compressed references, and pinned artifacts. It may be cached by
the host but remains reproducible.

### 24.7 SOUL rendering

SOUL files are generated from current accepted artifacts at a known ledger
watermark. Rendered files include or link to a generation manifest containing:

- artifact IDs;
- revision IDs;
- watermark;
- renderer version;
- content hash.

### 24.8 Projection read degradation

If a projection is stale or unavailable:

- APIs report the condition;
- critical current-state reads may query the ledger directly;
- semantic search may be unavailable rather than returning stale results where
  policy forbids staleness;
- no projection is allowed to invent missing semantic content.

---

## 25. Public API and SDK Requirements

### 25.1 API principles

Public APIs expose domain concepts and truthful completion state. They must not
expose internal queue-file paths, mutable index files, backend-specific IDs, or
integration-specific semantic shortcuts.

### 25.2 Observe event

```http
POST /v1/events
```

Request:

```yaml
tenant/workspace context: authenticated, not caller-spoofable
session_id: string
turn_id: string | null
event_kind: enum
actors: [ActorRef]
observed_at: timestamp | null
payload: object or content reference
source_refs: [SourceRef]
idempotency_key: string
```

Response:

```yaml
event_id: string
capture_status: persisted | duplicate
semantic_status: pending | succeeded | partial | retrying | terminal_failure
bead_id: string | null
annotation_job_id: string
ledger_position: integer
```

### 25.3 Semantic completion

```http
GET /v1/events/{event_id}/memory-status
```

Returns event, bead, optional-row, task-attempt, job, and projection status
without implying that durable capture equals semantic success.

### 25.4 Bead read and unpack

```http
GET /v1/beads/{bead_id}
POST /v1/beads/{bead_id}/unpack
```

Read returns the canonical bead and current visibility metadata. Unpack adds
full authorized source hydration and related current-state context.

### 25.5 Assertions and current state

```http
GET /v1/assertions/{assertion_id}
GET /v1/state?subject=...&slot=...&valid_at=...&known_at=...
GET /v1/state/ambiguities
```

Responses include:

- current/ambiguous/contested state;
- active terminals;
- revision path;
- valid and known time;
- supporting evidence IDs;
- ledger/projection watermark.

### 25.6 Associations

```http
GET /v1/associations?object_id=...&direction=out|in|both
GET /v1/association-coverage
POST /v1/association-review-runs
```

Reverse-direction output must identify that it is a computed inverse view of a
canonical forward assertion.

### 25.7 Artifacts

```http
GET /v1/artifacts
GET /v1/artifacts/{artifact_id}
POST /v1/artifact-runs
POST /v1/artifacts/{artifact_id}/review
POST /v1/artifacts/{artifact_id}/revise
```

Review/revision endpoints create semantic operations or explicit human-authored
events; they do not mutate Artifact rows in place.

### 25.8 Context promotion

```http
GET /v1/context/{session_id}
POST /v1/context/{session_id}/assemble
GET /v1/context/{session_id}/revisions
```

The current-context response identifies expanded versus compressed beads and
supports unpack links.

### 25.9 Retrieval

```http
POST /v1/retrieve
```

Request:

```yaml
query: string
session_id: string | null
valid_at: timestamp | null
known_at: timestamp | null
effort: low | standard | high | frontier
answer_schema: string | null
source_policy: object
```

Response:

```yaml
status: answered | abstained | pending | failed
answer: object | string | null
citations: [Citation]
current_state_refs: [ObjectRef]
ambiguities: [object]
limitations: [string]
retrieval_receipt: object
projection_watermarks: object
```

### 25.10 Jobs and operations

```http
GET /v1/jobs/{job_id}
GET /v1/semantic-tasks/{task_id}
POST /v1/jobs/{job_id}/retry
POST /v1/admin/reconcile
POST /v1/admin/projections/{name}/rebuild
```

Administrative mutations require scoped authority and append audit events.

### 25.11 SDK surface

The primary Python API should remain small:

```python
memory.observe(event)
memory.status(event_id)
memory.get_bead(bead_id, unpack=False)
memory.resolve(subject, slot, valid_at=None, known_at=None)
memory.retrieve(query, effort="standard", ...)
memory.list_artifacts(...)
memory.assemble_context(session_id, ...)
```

Framework adapters translate their events into this API. They must not call
internal persistence modules.

---

## 26. Integration and Adapter Contract

### 26.1 Adapter responsibilities

Adapters own:

- mapping host events to SourceEvent fields;
- preserving actor, turn, trace, and tool provenance;
- authentication and tenant/workspace context handoff;
- delivery idempotency keys;
- reporting capture versus semantic completion truthfully;
- rendering current context into host-supported formats;
- requesting unpack/retrieval through public APIs.

### 26.2 Adapter prohibitions

Adapters must not:

- construct canonical bead IDs;
- write SQL, graph, vector, or projection stores directly;
- submit pre-authored deterministic semantic fallback beads;
- default semantic labels;
- bypass job creation;
- treat HTTP acceptance as completed semantic memory;
- alter revision chains;
- carry caller-controlled tenant authority fields that override authentication.

### 26.3 Inline host authorship

A host agent may author the AnnotationBundle inline if it uses the same
canonical schema, evidence boundary, task receipt, and validation path. Inline
authorship must not become a second write pipeline.

The host submits the result as completion of the existing `annotate_event` Job
or through an equivalent task-completion API keyed to the SourceEvent and task
ID.

### 26.4 Hosted delegated authorship

Hosted delegation forwards only allowlisted tenant/workspace/model-routing
metadata. Provider credentials and authorization context remain controlled by
the hosted service. A failed delegated task stays retryable; shared-key fallback
must not silently cross tenant or quota policy.

---

## 27. Configuration

### 27.1 Invariants are not flags

The following cannot be disabled in canonical mode:

- one logical bead lineage and one current bead version per SourceEvent;
- optional assertions;
- LLM semantic authorship;
- no deterministic semantic fallback;
- append-only revisions/artifacts;
- one resolver;
- tenant isolation;
- truthful receipts;
- transactional mandatory Job creation;
- salience/truth separation.

### 27.2 Typed configuration groups

```yaml
core_memory:
  deployment: local | hosted | test
  ledger:
    backend: sqlite | postgres
    dsn_or_path: secret/reference
  semantic_runtime:
    provider: string
    endpoint: string | null
    model_policies: object
    retry_policy: object
  jobs:
    worker_concurrency: integer
    lease_seconds: integer
    reconciliation_interval_seconds: integer
  projections:
    vector: object
    graph: object
    lexical: object
  retrieval:
    default_effort: enum
    budgets: object
  context:
    token_budget: integer
    assembly_policy: object
  artifacts:
    review_policies: object
  privacy:
    encryption: object
    redaction: object
  integrations:
    named adapter configuration
```

### 27.3 Environment variable policy

Environment variables should be limited to:

- config location;
- secrets/credential references;
- database/service endpoints;
- deployment identity;
- emergency operational overrides that cannot alter semantic invariants.

Feature-specific environment flags should migrate into typed configuration or
be deleted.

### 27.4 Deployment presets

**Local**

- SQLite ledger;
- local worker process or in-process worker loop;
- optional local vector/graph projection;
- filesystem renderings as projections;
- one tenant boundary.

**Hosted**

- PostgreSQL ledger with row-level security;
- shared jobs table with horizontally scaled workers;
- managed vector/graph projections;
- delegated semantic runtime;
- tenant/workspace quotas and audit.

**Test**

- isolated temporary SQLite/PostgreSQL fixture;
- deterministic fake transport only for mechanical tests;
- recorded or live LLM outputs for semantic tests;
- no production fallback semantics.

---

## 28. Security, Privacy, and Authority

### 28.1 Tenant and workspace security

- Every request resolves tenant/workspace from authenticated context.
- Every ledger query includes enforced tenant/workspace scope.
- Semantic-task evidence packets contain only authorized rows.
- Job leasing and execution preserve tenant context.
- Cross-tenant IDs are treated as not found, not as accessible references.

### 28.2 Source-content minimization

Semantic tasks receive the minimum evidence needed for the operation while
preserving enough context for accurate judgment. Evidence selection itself may
be LLM-planned but is bounded and authorized deterministically.

### 28.3 Encryption

- SQL at-rest encryption uses platform facilities or application-level
  encryption for sensitive payloads.
- Source payloads and hydrated caches may be encrypted separately from concise
  bead metadata.
- Provider credentials never enter semantic prompts or ledger metadata.
- Backup encryption is required.

### 28.4 Redaction and deletion

Append-only semantic history must coexist with privacy obligations.

The privacy workflow may:

- cryptographically erase source payload encryption keys;
- append tombstone/redaction records;
- delete legally required rows under privileged audit;
- rebuild projections to remove content;
- preserve non-sensitive mechanical audit metadata where permitted.

Normal semantic correction must not use destructive deletion.

### 28.5 Prompt injection and untrusted sources

Hydrated documents and external records are evidence, not instructions.
Semantic prompts must clearly separate system policy, task instructions, and
untrusted evidence. Tool execution is not permitted from a semantic reasoning
task unless the operation contract explicitly provides controlled retrieval
tools.

### 28.6 Human authority

Human review actions record:

- actor identity;
- authority scope;
- decision;
- reason;
- target IDs;
- timestamp;
- optional evidence.

Human endorsement may change review/epistemic status through an append-only
event or Revision; it must not rewrite historical LLM output.

---

## 29. Observability and Audit

### 29.1 End-to-end trace

For any retrieval answer or current-state value, an operator must be able to
trace:

```text
answer statement
  -> cited assertion/artifact/bead
  -> revision/current-state path
  -> supporting associations/evidence set
  -> ObservationBead
  -> SourceEvent/EvidenceRef
  -> semantic task receipt(s)
```

### 29.2 Required operational metrics

**Capture and annotation**

- SourceEvents captured;
- duplicate captures;
- pending annotation count;
- annotation completion latency;
- retry distribution;
- invalid-output rate;
- terminal semantic failures;
- bead/source one-to-one violations.

**Assertions and revisions**

- assertions per bead distribution;
- zero-assertion rate;
- unsupported assertion evaluation rate;
- ambiguous/contested slot count;
- revision operations by type;
- broken/cyclic chain count;
- resolver consistency checks.

**Associations**

- eligible/reviewed bead coverage;
- candidate/accepted/rejected counts;
- oldest pending review;
- relationship and direction distributions;
- evidence-reference completeness;
- valid-time completeness;
- independent-source distribution.

**Artifacts**

- proposals by kind;
- verification and acceptance rates;
- time to review;
- supersession frequency;
- evidence-set completeness;
- deterministic-fallback count, which must remain zero.

**Promotion**

- context assembly frequency and latency;
- expanded/compressed counts;
- token savings;
- unpack requests/successes;
- task success and critical-context retention;
- promotion semantic failure/retry count.

**Retrieval**

- stage latency;
- candidates per collector;
- graph path counts/depth;
- hydration attempts/successes;
- evidence iteration count;
- answered/abstained/failed rate;
- citation verification failures;
- contested-state surfacing;
- projection staleness.

**Jobs and projections**

- state/age by job kind;
- lease recovery;
- reconciliation-created jobs;
- missing obligation count;
- projection lag/watermarks;
- rebuild duration/failure.

### 29.3 Audit views

Operator tooling must support:

- event-to-bead trace;
- task attempt history;
- invalid-output inspection;
- current-state explanation;
- relationship evidence inspection;
- artifact evidence and revision history;
- context-view revision diff;
- retrieval plan/evidence/citation trace;
- missing-job reconciliation report;
- projection-health report;
- migration provenance report.

### 29.4 No false success

Every API and metric must distinguish:

- captured;
- semantically authored;
- semantically verified;
- projected/indexed;
- current-state resolved;
- rendered/synchronized.

No umbrella `ok=true` may imply all stages completed unless the response
explicitly defines and proves those stages.

---

## 30. Semantic Quality and Evaluation Program

### 30.1 Evaluation philosophy

Mechanical unit tests are necessary but cannot validate semantic truth. Core
Memory requires a versioned evaluation corpus containing raw SourceEvents,
human- or independently-adjudicated gold annotations, expected ambiguity, and
accepted answer evidence.

### 30.2 Observation annotation benchmark

Measure:

- primary-label accuracy;
- title faithfulness;
- summary entailment;
- unsupported statement rate;
- source-span precision and recall;
- observation coverage;
- appropriate thin-bead rate;
- retrieval-eligibility correctness;
- optional assertion precision and recall;
- appropriate assertion abstention.

Mandatory adversarial case:

```text
SourceEvent: user says hello.
Forbidden output: latent goal to become a pilot or any unrelated durable fact.
Expected: thin greeting observation, zero assertions.
```

### 30.3 Claim and revision benchmark

Measure:

- atomic claim extraction precision/recall;
- subject/slot/value accuracy;
- scope accuracy;
- valid-time extraction accuracy;
- reaffirm/supersede/retract/contest/resolve decision accuracy;
- ambiguity preservation;
- current-state answer accuracy;
- as-of valid-time accuracy;
- as-of known-time accuracy.

Mandatory adversarial case:

```text
Claim 1: self.database = Postgres
Claim 2: self.database = SQLite
No Revision present
Expected current state: ambiguous
Forbidden: implicit latest-wins selection
```

### 30.4 Association benchmark

Measure separately:

- relevant-pair candidate recall;
- accepted-edge precision;
- predicate accuracy;
- direction accuracy;
- evidence-reference correctness;
- no-link accuracy;
- valid-time extraction;
- inverse-view correctness;
- contradiction detection;
- source-independence handling.

### 30.5 Artifact benchmark

For every artifact kind, measure:

- evidence sufficiency;
- unsupported synthesis rate;
- contradiction disclosure;
- alternative-explanation quality;
- artifact-kind correctness;
- title/content faithfulness;
- valid-time scope;
- revision recommendation accuracy;
- abstention/no-artifact accuracy;
- human/independent-judge agreement.

Dreamer/goal adversarial cases must include repeated lexical tokens that do not
constitute a meaningful goal.

### 30.6 Promotion benchmark

Use task-based evaluation:

- compare full context, LLM-promoted context, and random/heuristic baselines;
- measure downstream task success;
- critical fact/goal retention;
- irrelevant-content rate;
- token reduction;
- unpack effectiveness;
- stability when context changes;
- no correlation between compression and truth-state mutation.

### 30.7 Retrieval benchmark

Measure:

- semantic evidence recall@k;
- claim-current-state accuracy;
- causal-path evidence recall;
- source hydration completeness;
- answer factuality;
- citation precision/recall;
- temporal accuracy;
- ambiguity disclosure;
- appropriate abstention;
- end-to-end task success;
- degradation truthfulness.

The benchmark must exercise all retrieval stages and prove that causal
expansion and hydration are available through the one pipeline.

### 30.8 Live-model requirement

At least one release-gate suite must exercise the real semantic runtime. Tests
that inject deterministic prebuilt AnnotationBundles cannot demonstrate LLM
semantic quality.

Recorded-model fixtures may support deterministic regression tests, but they
must retain the real evidence packet, prompt version, model output, and
validation result.

### 30.9 Independent evaluation

Where feasible:

- author model and evaluation model differ;
- ambiguous cases receive human adjudication;
- gold labels include disagreement/uncertainty;
- evaluator prompts and rubrics are versioned;
- score changes are reported by task/model/prompt/schema version.

---

## 31. Performance and Scalability Requirements

### 31.1 Capture path

Targets excluding network variability:

- local SQLite SourceEvent capture p95: <= 100 ms;
- hosted PostgreSQL capture p95: <= 250 ms;
- SourceEvent and mandatory Job commit in one transaction;
- capture remains available when semantic providers are unavailable.

### 31.2 Semantic completion

Semantic latency depends on model/provider. Core Memory must report separately:

- queue wait;
- provider latency;
- validation/commit latency;
- projection latency.

Default operational targets:

- standard event annotation p95 completion: <= 30 seconds when provider healthy;
- oldest mandatory pending annotation: alert at 5 minutes;
- projection currentness after semantic commit: p95 <= 60 seconds;
- retries must not block unrelated tenants or jobs.

### 31.3 Ledger lookup

Targets at supported reference scale:

- bead archive lookup p95 local: <= 100 ms;
- bead archive lookup p95 hosted: <= 300 ms;
- scoped current-state resolution p95: <= 200 ms excluding hydration;
- as-of resolver performance must use indexed intervals and revision scope.

### 31.4 Retrieval

Infrastructure overhead excluding LLM and external connectors:

- candidate collection p95: <= 750 ms at standard effort;
- graph expansion p95: <= 500 ms within standard depth budget;
- ledger source lookup p95: <= 300 ms;
- full response reports stage timing.

No stage target justifies deterministic semantic fallback.

### 31.5 Job throughput

- worker concurrency is configurable by deployment;
- job leasing avoids global locks;
- tenant fairness prevents one large backlog from starving others;
- priority cannot permanently starve low-priority mandatory work;
- reconciliation can process all active tenants incrementally;
- job payloads reference large evidence rather than duplicating it.

### 31.6 Projection scale

Projectors consume ordered ledger positions in batches and resume from
watermarks. Rebuilds support tenant-scoped and full-dataset modes. A projection
rebuild must not block canonical capture.

---

## 32. Reliability, Failure, and Recovery

### 32.1 Semantic provider unavailable

- SourceEvent persists.
- Annotation Job remains retryable.
- API reports semantic pending/retrying.
- No bead or fallback semantic output is created.
- Alerting uses oldest-pending and provider-error rates.

### 32.2 Invalid LLM output

- Receipt stores raw-output hash and validation errors.
- Retry supplies schema errors and unchanged evidence boundary.
- No invalid semantic row becomes visible.
- Repeated invalid output may route to a stronger model, not deterministic
  meaning.

### 32.3 Database failure

- transaction rollback leaves neither partial canonical rows nor orphaned
  mandatory Jobs;
- retries use idempotency keys;
- hosted failover preserves ledger ordering and tenant isolation;
- backups and point-in-time recovery are required before cutover.

### 32.4 Worker crash

- expired lease returns Job to eligible state;
- handler idempotency prevents duplicate semantic rows;
- commit-before-acknowledgement is safe;
- task receipts reveal multiple attempts.

### 32.5 Projection failure

- canonical write remains committed;
- projection Job retries;
- watermark reports degradation;
- direct ledger reads remain available where designed;
- projection can be deleted and rebuilt.

### 32.6 Resolver corruption

- resolver is pure and can be rerun against ledger rows;
- projection consistency tests compare materialized and direct resolution;
- broken chains return `invalid`, not guessed truth;
- repair requires explicit maintenance or semantic Revision.

### 32.7 Source unavailable

- hydrated source is marked unavailable;
- bead and source hash remain;
- retrieval LLM reassesses sufficiency;
- answer may abstain;
- no summary is relabeled as verified source evidence.

### 32.8 Model regression

- prompt/model/schema versions allow bisecting quality;
- evaluation gates block rollout;
- tasks may be re-run against the same immutable evidence with a new version;
- new output creates revisions or replacement objects as necessary;
- historical authored results remain auditable.

### 32.9 Disaster recovery

Recovery order:

1. Restore SQL ledger and operational Job table.
2. Validate canonical row counts, constraints, and hashes.
3. Reset/reconcile expired leases.
4. Rebuild current-state projections.
5. Rebuild search/vector indexes.
6. Rebuild graph projection.
7. Rebuild hot-context and SOUL renderings.
8. Resume external projection sync.

JSONL is not part of the recovery source of truth.

---

## 33. Migration From JSONL and Current Runtime

### 33.1 Migration principles

- Retire JSONL completely as live state.
- Preserve every recoverable canonical object and provenance reference.
- Do not upgrade heuristic/fallback semantics to LLM-authored authority.
- Make migration repeatable in a staging copy and idempotent by legacy ID/hash.
- Produce a complete migration ledger and discrepancy report.
- Cut over once; do not maintain indefinite dual write.

### 33.2 Inventory

Migration tooling inventories:

- `.beads/index.json`;
- session/global bead JSONL;
- turn/session archives;
- claims and claim updates embedded in beads;
- association records and lifecycle state;
- archive snapshots/indexes;
- semantic task receipts;
- Dreamer candidates/projections;
- SOUL revisions/files;
- promotion decisions and heads;
- myelination/retrieval feedback;
- queue and projection manifests;
- vector/graph IDs and source hashes.

### 33.3 Classification

Every legacy row is classified as:

| Class | Treatment |
|---|---|
| Canonical source evidence available | Import SourceEvent and linked semantic row with original provenance |
| Agent/LLM-authored with valid receipt | Import with attributed semantic origin |
| Human-authored/approved | Import with human authority and audit fields |
| Deterministic fallback/heuristic | Import as legacy artifact/observation metadata, excluded from current truth until re-authored |
| Structurally incomplete | Quarantine with migration issue record |
| Duplicate projection/cache | Do not import as canonical; rebuild from ledger |
| Orphaned/unresolvable | Preserve in quarantine export and discrepancy report |

### 33.4 SourceEvent reconstruction

Where original turns/documents exist, construct SourceEvents with stable
content hashes and source coordinates. Where source evidence is unavailable,
do not pretend the bead itself is original evidence. Mark source limitation and
require re-authoring policy before current-truth use.

### 33.5 Bead migration

- one legacy canonical bead maps to one logical ObservationBead lineage when an
  attributable observed event can be identified;
- derived companion beads map to Artifact proposals or quarantined legacy
  semantics, not new ObservationBeads;
- mixed source/semantic types are separated into SourceEvent source kind and
  reduced observation label/facets;
- unmappable type choices retain original values in migration metadata and
  require LLM re-authoring rather than deterministic semantic conversion.

### 33.6 Claims and revisions

- import claims as assertions with preserved IDs where safe;
- import explicit updates as Revisions;
- do not default missing update decisions;
- run the one resolver after import;
- multiple unlinked incompatible terminals become ambiguous;
- discrepancy report lists every slot whose pre-migration resolver output
  differs from the new explicit-chain result.

### 33.7 Associations

- import canonical LLM/human-authored edges with provenance;
- import deterministic preview edges only as legacy noncanonical records or
  schedule re-review;
- normalize direction through an LLM-assisted migration task when semantic
  direction is ambiguous;
- preserve original valid/system time where known;
- rebuild graph projections from imported assertions.

### 33.8 Artifacts and SOUL

- Dreamer candidates, accepted goals, storylines, and SOUL revisions map into
  Artifact and Revision rows;
- deterministic/template-generated content remains proposal-only or requires
  re-authoring;
- SOUL files are regenerated from accepted current artifacts after cutover;
- original files are retained as migration evidence, not live truth.

### 33.9 Promotion and context

Legacy promotion history may be imported as ContextViewRevision history where
the selection and representation can be reconstructed. Promotion-derived truth
confidence is discarded or retained only as legacy metadata. Full beads remain
in the archive.

### 33.10 Migration verification

Required checks:

- source event counts and content hashes;
- bead counts and one-to-one mappings;
- assertion and revision counts;
- ambiguous slot inventory;
- association counts by provenance;
- artifact counts and current lineages;
- archive lookup/unpack success;
- tenant/workspace isolation;
- projection rebuild success;
- representative retrieval parity and quality improvement;
- zero JSONL reads/writes in canonical runtime after cutover.

### 33.11 Cutover

1. Freeze semantic writes or capture a consistent database/import boundary.
2. Back up all legacy data.
3. Run import into staging SQL ledger.
4. Run full verification and semantic re-authoring queues where required.
5. Run user/tenant canary.
6. Switch canonical APIs to SQL ledger.
7. Reconcile Jobs and rebuild projections.
8. Monitor release gates.
9. Remove JSONL runtime code after rollback window closes.

### 33.12 Rollback

Rollback restores the pre-cutover application and legacy backup only during the
bounded migration window. New SQL-ledger writes must be exported or replayed
through an explicit rollback tool; silent dual-write is prohibited.

---

## 34. Implementation Program

### Phase 0 — Specification, invariants, and evaluation baseline

Deliverables:

- approve this PRD;
- freeze v1 schemas and reduced vocabularies;
- build evaluation corpus and adversarial cases;
- record current runtime quality/latency baseline;
- inventory all canonical and projection state;
- add architecture tests preventing new JSONL/index authority paths;
- define legacy provenance classification.

Exit gates:

- product decisions resolved;
- evaluation dataset versioned;
- schema review complete;
- migration inventory complete;
- no unresolved definition of observation, assertion optionality, revision, or
  promotion.

### Phase 1 — SQL ledger and single jobs table

Deliverables:

- SQLite/PostgreSQL logical schema;
- Ledger and JobStore ports;
- transactional event+job append;
- ledger positions and projection watermarks;
- tenant enforcement;
- backup/recovery tooling;
- migration importer skeleton.

Exit gates:

- local and hosted contract tests pass;
- atomicity/idempotency property tests pass;
- one jobs table leases safely under concurrency;
- canonical roles cannot update/delete semantic rows.

### Phase 2 — Single semantic operation runtime

Deliverables:

- canonical request/result/receipt contracts;
- provider and delegated adapters;
- multi-step operation support;
- retry/routing behavior;
- invalid-output correction loop;
- removal of semantic fallback modes in new path.

Exit gates:

- all operation attempts receipted;
- provider-unavailable test leaves pending work and no semantic output;
- live-model annotation smoke passes;
- tenant routing canary passes.

### Phase 3 — Observation write pipeline

Deliverables:

- SourceEvent API;
- AnnotationBundle;
- one event/one bead-lineage and one-current-version constraints;
- EvidenceRef validation;
- optional assertion handling;
- truthful capture/semantic receipts;
- adapter completion interface.

Exit gates:

- greeting adversarial test passes;
- zero-assertion bead accepted;
- unsupported derived bead rejected;
- duplicate events do not duplicate beads;
- all adapters use the same pipeline.

### Phase 4 — Assertion/revision ledger and resolver

Deliverables:

- claim and association assertion tables;
- Revision schema;
- pure unified resolver;
- bi-temporal as-of API;
- ambiguity/contest state;
- current-state projection.

Exit gates:

- duplicate resolver implementations removed from canonical path;
- unlinked incompatible-claim test returns ambiguous;
- explicit supersession resolves correctly;
- valid/known-time matrix passes;
- cyclic chains return invalid.

### Phase 5 — Association coverage and evidence aggregation

Deliverables:

- neutral candidate collectors;
- LLM association review operation;
- canonical directional predicates;
- inverse traversal;
- EvidenceSet aggregation;
- operational coverage projection;
- re-review/versioning.

Exit gates:

- no deterministic relation writes;
- direction and valid-time benchmark gates pass;
- rejected candidates do not become edges;
- source independence is preserved;
- graph projection rebuild succeeds from SQL.

### Phase 6 — Unified artifact system

Deliverables:

- Artifact persistence and review;
- generic synthesis/revision operations;
- Dreamer policies on top of artifacts;
- goal/storyline/SOUL migrations;
- current-artifact projections;
- SOUL rendering.

Exit gates:

- deterministic Dreamer/goal/SOUL content generators removed from canonical
  path;
- artifact mutation produces replacement+Revision;
- rendered SOUL can be rebuilt from ledger;
- artifact evidence/verification gates pass.

### Phase 7 — Promotion and hot context

Deliverables:

- LLM context assembly operation;
- ContextViewRevision;
- deterministic token packer;
- expanded/compressed rendering;
- unpack API;
- context revision diff/audit.

Exit gates:

- full archive remains unchanged by promotion;
- compressed references unpack successfully;
- promotion does not affect truth/current-state fields;
- downstream task benchmark meets quality/token targets.

### Phase 8 — Unified retrieval pipeline

Deliverables:

- LLM RetrievalPlan;
- unified candidate collectors;
- resolver integration;
- causal/association expansion;
- source hydration;
- iterative evidence judgment;
- answer synthesis/abstention;
- citation verification.

Exit gates:

- all public recall/search/causal answer paths route through one pipeline;
- all retrieval capabilities are available;
- no deterministic fallback answer exists;
- benchmark gates pass;
- ambiguity and source limitations surface correctly.

### Phase 9 — Projection, worker, and configuration consolidation

Deliverables:

- one worker executable/registry;
- all old queue types migrated;
- projection runners and reconciliation;
- typed configuration groups/presets;
- removal of invariant-changing flags;
- integration adapter boundary cleanup.

Exit gates:

- no legacy queue files are active;
- missing-obligation reconciliation produces zero gaps;
- projection delete/rebuild drills pass;
- configuration compatibility audit complete.

### Phase 10 — Legacy migration and deletion

Deliverables:

- production-grade migration tool;
- tenant canary and reports;
- cutover/rollback runbooks;
- JSONL and mutable index removal;
- removal of duplicate resolvers, pipelines, candidate stores, and fallbacks;
- documentation/status truth update.

Exit gates:

- migration acceptance criteria pass;
- zero canonical runtime JSONL access;
- zero direct `.beads/index.json` access;
- no active semantic fallback modes;
- old runtime code deleted rather than left as default-off compatibility paths;
- full release gates pass.

---

## 35. Target Module Boundaries

An illustrative target package structure:

```text
core_memory/
  domain/
    events.py
    beads.py
    assertions.py
    revisions.py
    artifacts.py
    context.py
    vocabulary.py
    resolver.py

  ledger/
    protocol.py
    sqlite.py
    postgres.py
    schema/
    migrations/

  semantic/
    contracts.py
    runtime.py
    receipts.py
    operations/
      annotate_event.py
      associations.py
      artifacts.py
      context.py
      retrieval.py
      verification.py

  jobs/
    model.py
    store.py
    worker.py
    handlers/
    reconciliation.py

  projections/
    base.py
    current_state.py
    search.py
    graph.py
    context.py
    soul.py

  retrieval/
    pipeline.py
    collectors.py
    expansion.py
    hydration.py
    citations.py

  api/
    service.py
    receipts.py

  integrations/
    ... adapters only ...
```

### 35.1 Dependency direction

```text
domain
  <- ledger interfaces
  <- semantic/job/projection/retrieval services
  <- API
  <- integrations
```

Concrete providers are registered or injected through ports. Persistence must
not import runtime/integration implementations to trigger side effects.

### 35.2 Small public surface

The top-level library should expose the small SDK described in Section 25.11.
Administrative and projection APIs remain explicitly namespaced.

### 35.3 Compatibility policy

Compatibility adapters may translate legacy calls into SourceEvents during the
migration window. They must not preserve old semantic mutation behavior. After
the announced window, they are deleted.

---

## 36. Detailed Acceptance Criteria

### 36.1 Observation capture

- [ ] Every accepted SourceEvent atomically creates one `annotate_event` Job.
- [ ] Replaying an idempotency key returns the same event/job.
- [ ] Exactly one logical ObservationBead lineage can reference a SourceEvent,
      with exactly one current immutable version.
- [ ] Every bead label/title/summary is grounded by valid EvidenceRefs.
- [ ] Invalid annotation output creates no fallback bead.
- [ ] A zero-assertion AnnotationBundle succeeds.
- [ ] Derived companion beads are not part of the canonical model.
- [ ] Capture and semantic completion are distinct in every API receipt.

### 36.2 Semantic runtime

- [ ] Every LLM operation uses the common request/result/receipt contract.
- [ ] No canonical semantic operation accepts a deterministic fallback mode.
- [ ] Provider unavailability leaves work retryable and creates no semantics.
- [ ] Invalid output retries with validation feedback.
- [ ] Every attempt has immutable model/prompt/schema/evidence provenance.
- [ ] Tenant/workspace routing is enforced and tested.

### 36.3 Ledger

- [ ] SQLite and PostgreSQL pass the same ledger contract suite.
- [ ] Canonical semantic rows are append-only.
- [ ] Revision/current-state changes do not mutate original semantic rows.
- [ ] Mandatory downstream Jobs are created transactionally.
- [ ] Canonical APIs perform no JSONL or `.beads/index.json` reads/writes.
- [ ] Projection loss is recoverable entirely from SQL.
- [ ] Tenant isolation is database-enforced in hosted mode.

### 36.4 Assertions and revisions

- [ ] Assertions are optional bead outputs.
- [ ] Missing update decision is rejected, not defaulted.
- [ ] Multiple incompatible unlinked terminals resolve to `ambiguous`.
- [ ] Explicit supersede/retract/contest/resolve operations behave as specified.
- [ ] Exactly one resolver powers all current-state consumers.
- [ ] Valid-time and known-time queries return correct historical state.
- [ ] Cyclic/broken chains return `invalid` and never guessed truth.

### 36.5 Relationships

- [ ] Relationship predicate and direction are LLM-authored.
- [ ] Canonical associations are stored only once in forward direction.
- [ ] Inverse traversal is computed correctly.
- [ ] `valid_from`/`valid_to` remain unknown when evidence does not supply them.
- [ ] System/recorded time is assigned mechanically.
- [ ] No deterministic candidate signal becomes a relationship.
- [ ] Association coverage and quality metrics are independently reported.
- [ ] Aggregate evidence preserves source independence and contradiction.

### 36.6 Artifacts

- [ ] All required artifact kinds use one Artifact schema/lifecycle.
- [ ] Artifact mutation creates replacement+Revision.
- [ ] Dreamer/goal/storyline/SOUL semantic content has an LLM receipt.
- [ ] Missing/blocked semantic output creates no artifact content.
- [ ] EvidenceSet and limitations are present where required.
- [ ] SOUL renderings rebuild from accepted current artifacts.
- [ ] Human edits re-enter as SourceEvents.

### 36.7 Promotion

- [ ] Promotion ranking is LLM-authored.
- [ ] Deterministic code only validates IDs and packs token budgets.
- [ ] Full beads remain in the archive after compression.
- [ ] Compressed representation contains ID, type/label, and associations.
- [ ] Every compressed bead can be unpacked.
- [ ] ContextViewRevisions are append-only and diffable.
- [ ] Promotion cannot change current truth, evidence, or semantic authority.

### 36.8 Retrieval

- [ ] One public pipeline handles semantic, claim, causal, hydrated retrieval.
- [ ] LLM authors query plan, evidence relevance, sufficiency, and synthesis.
- [ ] Semantic search, causal expansion, association expansion, current-state
      resolution, and source hydration are all available.
- [ ] Deterministic keyword logic cannot disable a semantic capability.
- [ ] Iterative retrieval is supported within bounded effort.
- [ ] Unsupported answers abstain.
- [ ] Every citation passes deterministic verification.
- [ ] Ambiguity/contestation and source limitations surface to the caller.

### 36.9 Jobs and projections

- [ ] All background work uses one jobs table and worker protocol.
- [ ] Multiple worker replicas lease without duplicate canonical output.
- [ ] Reconciliation detects and repairs every missing mandatory obligation.
- [ ] Job and projection coverage metrics exist by kind.
- [ ] Every projection exposes a ledger watermark.
- [ ] Delete/rebuild drills pass for vector, graph, current-state, hot-context,
      and SOUL projections.

### 36.10 Migration

- [ ] Legacy state inventory and provenance classification complete.
- [ ] Source and semantic object counts/hashes reconcile.
- [ ] Deterministic legacy semantics are not upgraded to authored truth.
- [ ] Unlinked claim conflicts become visible ambiguity.
- [ ] Full bead archive lookup succeeds after import.
- [ ] User/tenant canary passes.
- [ ] JSONL runtime code is removed after rollback window.

---

## 37. Test Plan

### 37.1 Domain unit tests

- vocabulary validation;
- EvidenceRef coordinate validation;
- temporal interval rules;
- inverse predicate mapping;
- revision compatibility and cycle detection;
- resolver terminal-state matrix;
- ContextViewRevision constraints;
- Artifact lineage;
- canonical-ID and idempotency rules.

### 37.2 Database contract tests

Run identically against SQLite and PostgreSQL:

- event+job atomicity;
- annotation bundle atomicity;
- tenant isolation;
- append-only enforcement;
- uniqueness/idempotency;
- concurrent revision sequencing;
- concurrent job leasing;
- crash/retry behavior;
- ledger-position ordering;
- backup/restore and projection replay.

### 37.3 Property tests

- any revision graph either resolves to a defined state or returns invalid;
- no resolver input order changes output;
- inverse traversal never creates a second canonical assertion;
- no projection-only mutation changes canonical query result;
- replay of ledger rows produces identical projection state;
- repeated job execution produces no duplicate canonical semantic row;
- compressed context always retains unpackable IDs;
- token packer never changes LLM rank order except mandatory pinned policy.

### 37.4 Semantic contract tests

Use recorded and live model runs:

- observation faithfulness;
- optional assertion abstention;
- claim/revision decisions;
- association no-link behavior;
- directional and temporal extraction;
- artifact support/limitations;
- promotion relative value;
- retrieval planning/evidence/synthesis;
- verifier behavior.

### 37.5 Adversarial tests

- greeting cannot create an unrelated goal;
- repeated keyword cannot become a latent goal without meaningful evidence;
- chronology cannot become causal direction;
- missing relation cannot become `similar_to` or generic association;
- missing claim decision cannot become reaffirm;
- competing unlinked claims cannot silently latest-win;
- source hydration failure cannot become verified evidence;
- provider failure cannot write deterministic SOUL text;
- recall frequency cannot upgrade truth status;
- compressed bead cannot become inaccessible;
- cross-tenant evidence reference is rejected;
- prompt-injected document cannot override semantic task instructions.

### 37.6 Integration tests

- host event through capture, annotation, projection, context, retrieval;
- inline and delegated authorship converge on the same pipeline;
- association review and graph projection;
- claim revision and as-of resolution;
- artifact synthesis/review/rendering;
- promotion/compression/unpack;
- job reconciliation after injected missing Jobs;
- projection deletion and rebuild;
- provider outage and recovery;
- hosted tenant routing.

### 37.7 Migration tests

- representative legacy fixture classes;
- duplicate IDs and partial rows;
- derived companion bead conversion;
- deterministic fallback quarantine;
- claim conflict exposure;
- association direction migration;
- SOUL/artifact lineage;
- count/hash reconciliation;
- cutover/rollback rehearsal.

### 37.8 End-to-end product tests

Scenarios must include:

- user preference changing over time;
- time-bounded operational state;
- goal adoption, progress, and abandonment;
- contradictory evidence from independent sources;
- multi-session storyline synthesis;
- SOUL identity/value revision;
- hot-context pressure and later unpack;
- causal question requiring graph expansion and document hydration;
- ambiguous question requiring abstention or qualification.

---

## 38. Metrics and Release Gates

### 38.1 Semantic release gates

Exact numeric thresholds must be set from the Phase 0 baseline, but release
must require:

- unsupported bead assertion rate below agreed maximum;
- summary entailment above agreed minimum;
- source-reference correctness above agreed minimum;
- claim revision accuracy above agreed minimum;
- zero implicit latest-wins outcomes in invariant tests;
- association direction and evidence precision above agreed minimum;
- artifact unsupported-synthesis rate below agreed maximum;
- retrieval citation precision above agreed minimum;
- appropriate abstention above agreed minimum;
- promotion task success non-inferior to full-context baseline within agreed
  margin while achieving material token reduction.

### 38.2 Operational release gates

- zero canonical JSONL/index runtime access;
- zero deterministic semantic fallback outputs;
- zero duplicate beads per SourceEvent;
- zero unresolved missing mandatory Job obligations after reconciliation;
- zero resolver inconsistencies;
- zero cross-tenant access failures in security suite;
- provider outage preserves capture and retryability;
- projection rebuild drills succeed;
- migration reconciliation passes for canary tenant;
- backup/restore drill succeeds.

### 38.3 Canary gates

Before broad rollout, a canary tenant must demonstrate:

1. live SourceEvent capture;
2. real LLM annotation;
3. zero-assertion case;
4. claim supersession and ambiguity;
5. directional/bi-temporal association;
6. artifact proposal and supersession;
7. promotion/compression/unpack;
8. semantic+causal+hydrated retrieval;
9. provider failure and retry recovery;
10. projection rebuild from SQL;
11. no JSONL/index dependency.

### 38.4 Release reporting

Release report includes:

- commit/deployment/schema versions;
- migrated tenant counts;
- ledger/projection watermarks;
- semantic quality scores and deltas;
- operational SLOs;
- known limitations;
- retry backlog;
- ambiguity/contest inventory;
- rollback readiness.

---

## 39. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM annotation latency delays usable memory | High | Durable capture first, async jobs, retries, model routing, truthful pending state |
| LLM variability changes labels/assertions | High | Small ontology, grounded evidence, versioned prompts, independent eval, revision/re-authoring |
| SQL migration loses legacy context | Critical | Full inventory, backups, hash/count reconciliation, quarantine, canary, bounded rollback |
| Optional assertions reduce recall richness | Medium | Precision-first evaluation, later association/artifact synthesis, source hydration |
| One pipeline becomes monolithic | High | Composable stage ports under one orchestrator and one truth policy |
| One jobs table becomes bottleneck | Medium | Indexed leasing, batching, partitioning where needed, multiple worker replicas |
| Artifact unification erases domain nuance | Medium | Typed content schemas/prompts while sharing lifecycle and persistence |
| Reduced ontology loses query precision | Medium | Orthogonal facets/qualifiers, eval-driven vocabulary freeze, extensions with governance |
| Bi-temporal schema becomes difficult to use | Medium | One temporal API, explicit field semantics, property tests, indexed interval helpers |
| Direction migration corrupts edges | High | Preserve originals, LLM-assisted rejudgment when ambiguous, canary graph comparisons |
| Promotion drops important hot context | High | LLM ranking, pinned requirements, task eval, immediate unpack, lossless archive |
| Provider outage creates unbounded backlog | High | Capture remains durable, alerting, routing, scalable workers, operator prioritization |
| Semantic retry cost runaway | Medium | Attempt/cost telemetry, stronger-model escalation policy, operator quarantine—not fallback |
| Projection staleness misleads retrieval | High | Watermarks, direct ledger fallback for critical state, degradation reporting |
| Append-only history conflicts with privacy deletion | Critical | Privileged audited erasure/redaction workflow and projection rebuild |
| Integration adapters bypass canonical pipeline | High | Small public API, boundary tests, remove internal imports, review enforcement |
| Legacy compatibility never gets deleted | High | Explicit migration window, deletion gates, architecture tests |

---

## 40. Rejected Alternatives

### 40.1 Keep JSONL for local simplicity

Rejected. JSONL inspired the original design but cannot provide the desired
transactionality, concurrency, constraints, one-resolver guarantees, or
projection discipline without rebuilding a database around files.

### 40.2 Maintain SQL and JSONL dual write permanently

Rejected. Permanent dual write creates two authorities and introduces the drift
this redesign is intended to eliminate.

### 40.3 Require claims/assertions on every bead

Rejected. This creates incentive to fabricate semantic richness and violates
the observation-note model.

### 40.4 Use deterministic semantic fallback for availability

Rejected. Availability of incorrect meaning is worse than truthful pending
state. Retries and provider routing solve operational availability without
manufacturing truth.

### 40.5 Keep feature-specific artifact stores

Rejected. Goals, storylines, Dreamer, and SOUL need different content schemas
but not different evidence, revision, receipt, review, and persistence systems.

### 40.6 Store inverse associations as duplicate edges

Rejected. Duplicate edges can diverge in evidence, time, and lifecycle. Store
one canonical direction and compute inverse traversal.

### 40.7 Let latest claim always win

Rejected. Ordering is not semantic supersession. Competing terminals without a
Revision are ambiguous.

### 40.8 Remove promotion

Rejected. Promotion is a valuable relative-context function and is lossless
when separated from archive retention and truth.

### 40.9 Make every retrieval query maximum effort

Rejected. All capabilities must be available, but the LLM planner should select
appropriate budgets. Maximum work on every query is unnecessarily slow and
expensive.

### 40.10 Use separate workers for every feature

Rejected. Specialized handlers and horizontal replicas are sufficient. One Job
protocol is simpler to monitor, reconcile, and recover.

### 40.11 Keep legacy runtime branches indefinitely

Rejected. Legacy data is migrated. Permanent branches multiply semantic
behavior and prevent the target architecture from becoming reliable.

---

## 41. Resolved Design Clarifications

1. **Are assertions required?** No. Only the ObservationBead is required after
   successful semantic annotation.
2. **Does every event create a bead?** Every accepted observable event creates
   exactly one logical bead lineage and one current immutable version once
   annotation succeeds.
3. **What happens while annotation is unavailable?** SourceEvent persists and
   Job retries; no semantic bead is fabricated.
4. **Can deterministic code make conditional decisions?** It may make
   mechanical conditions; all meaning-bearing conditions belong to the LLM.
5. **Is JSONL retained?** No, except optional explicit export. It is not a live
   authority or recovery source.
6. **How are mutations append-only?** A new record plus Revision is appended;
   current/superseded flags are projections.
7. **Are temporal and directional details semantic?** The LLM interprets
   evidenced meaning; the schema and traversal mechanics are deterministic.
8. **Is promotion removed?** No. It becomes LLM-authored lossless context
   assembly.
9. **Are compressed beads deleted?** No. They remain full in the archive and
   are represented by unpackable references in hot context.
10. **Does retrieval retain all features?** Yes. Semantic search, current state,
    causal/association expansion, and source hydration are part of one pipeline.
11. **How are all background tasks guaranteed?** Transactional Job creation,
    dependency fan-out, idempotency, and reconciliation sweeps.
12. **Can multiple worker processes run?** Yes. They share one table and
    protocol.
13. **Does myelination disappear?** Its navigation/salience capability remains;
    it cannot change truth confidence.
14. **Are SOUL files deleted?** No. They become rebuildable renderings of current
    accepted artifacts.

---

## 42. Open Implementation Decisions

These are implementation choices, not unresolved product invariants.

1. Exact v1 observation labels after benchmark validation.
2. Exact v1 association predicate/qualifier registry after migration analysis.
3. SQL typed-child-table versus constrained JSON-column balance.
4. Opaque ID format and tenant-scoped ledger-position implementation.
5. Whether invalid optional bundle rows commit a partial bead or force full
   bundle retry; this PRD recommends partial bead plus child repair Jobs.
6. Model/provider policy by semantic operation and authority tier.
7. Which artifact kinds require independent semantic verification or human
   review by default.
8. Context assembly cadence and default token budgets by integration.
9. Projection staleness thresholds by API/read class.
10. Exact migration treatment for legacy rows whose source evidence cannot be
    reconstructed.
11. PostgreSQL partitioning strategy at scale.
12. Whether local in-process workers are default or opt-in, provided they use
    the same JobStore/handler protocol.

Every choice must preserve the governing invariants.

---

## 43. Definition of Done

This program is complete only when all of the following are true:

1. Core Memory stores canonical state in SQLite/PostgreSQL, not JSONL or a
   mutable JSON index.
2. Every accepted SourceEvent atomically schedules semantic annotation.
3. Exactly one grounded ObservationBead lineage and one current immutable
   version exist per successfully annotated event.
4. Assertions are optional and zero-assertion events are first-class successes.
5. Every meaning-bearing output has LLM authorship and evidence provenance.
6. No deterministic semantic fallback remains on a canonical path.
7. All semantic failures produce pending/retryable/failed state rather than
   invented content.
8. Claims and associations use append-only assertions and explicit revisions.
9. One resolver controls all current-state and as-of behavior.
10. Unresolved incompatible terminals remain ambiguous or contested.
11. Association direction and bi-temporal behavior are canonical schema
    features with deterministic traversal.
12. Aggregate evidence preserves support, contradiction, independence, and
    temporal distribution.
13. Dreamer, goals, storylines, lessons, principles, identity, values, tensions,
    and SOUL use one Artifact system.
14. Artifact changes append replacements and Revisions rather than mutating
    canonical rows.
15. Promotion remains LLM-authored, reversible, and lossless.
16. Compressed beads can always be unpacked from the full archive.
17. One retrieval pipeline provides semantic search, current-state resolution,
    causal/association expansion, source hydration, LLM judgment, synthesis,
    and citation verification.
18. No deterministic retrieval answer fallback remains.
19. All deferred work uses one jobs table and one worker protocol.
20. Transactional obligations and reconciliation prove complete task coverage.
21. Every projection can be deleted and rebuilt from the ledger.
22. SOUL and hot-context files/views are projections, not truth authorities.
23. Local and hosted implementations pass the same contract tests.
24. Migration preserves provenance and does not upgrade heuristic semantics.
25. JSONL/index compatibility runtime code is deleted after cutover.
26. Semantic quality, operational reliability, security, performance,
    migration, and canary release gates all pass.
27. Canonical documentation and status pages accurately describe the shipped
    architecture.

At completion, every durable meaning in Core Memory must be traceable through:

```text
one evidence boundary
  -> one attributed semantic operation
  -> one append transaction
  -> one revision model
  -> one resolver
```

That is the simplicity and reliability contract for the next Core Memory
architecture.
