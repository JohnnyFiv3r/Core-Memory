# PRD: Agent-Led Semantic Write Integrity

**Status:** Implementation complete — copied/live hosted rollout pending

**Date:** 2026-07-10

**Owner surface:** Canonical per-turn write path, semantic bead persistence,
association judgment, promotion judgment, and write receipts

**Primary invariant:** Core Memory supplies typed guardrails and deterministic
runtime mechanics; agents author semantic meaning

**Related:** `AGENT_INSTRUCTIONS.md`, `docs/architecture_overview.md`,
`docs/write_side_flow.md`, `agentic-semantic-task-runtime.md`,
`session-enrichment-delta-slice-b.md`

---

## 1. Summary

Core Memory is intended to be an agent-led causal memory engine. The runtime
owns schemas, validation, identifiers, timestamps, idempotency, persistence,
queueing, session flush, indexing, compaction, and audit. The authoring agent
owns the meaning of each turn: bead type and content, retrieval framing,
claims, causal evidence, semantic associations, and promotion judgment.

The current runtime does not consistently enforce that division of labor. The
repository already contains most of the required machinery:

- `BEAD_AUTHORING_SPEC` describes per-type agent authorship;
- `metadata.crawler_updates` carries agent-authored updates internally;
- `validate_agent_authored_updates(...)` validates authored bead and association
  rows;
- `add_bead_for_store(...)` accepts nearly the entire canonical `Bead` schema;
- the side-effect queue is durable and idempotent;
- agent-issued promotion decisions already have an application path.

The failure is between those working pieces. Public adapter surfaces do not
expose the authored payload as a typed field, the authoring spec is not injected
into the primary agent, a 23-field normalizer drops most of the 147-field bead
schema, default configuration permits deterministic fallback authorship,
retrieval eligibility is forced on, association judging sees an impoverished
and chronology-biased candidate set, and receipts report success before the
canonical semantic bead is confirmed.

This PRD closes those gaps without replacing the queue or creating a parallel
memory architecture. It makes the existing agent-authored path the required
default, makes persistence lossless, restricts deterministic logic to
structural or advisory work, and gives callers truthful semantic completion
receipts.

---

## 2. Product invariant

For every finalized top-level turn, the authoring agent **must attempt exactly
one typed canonical current-turn bead write**. The same typed write may also
contain up to two explicitly derived companion beads when the turn supports
more than one durable semantic result.

The agent should fill every meaning-bearing field justified by the evidence in
the finalized turn. It must not invent content merely to fill the schema. A
valid turn bead may therefore be thin or rich:

- a **thin bead** faithfully preserves the turn but is normally not retrieval
  eligible;
- a **rich bead** contains sufficient retrieval framing, evidence, claims,
  state, or causal rationale to support durable recall.

Core Memory validates the authored payload and applies structural invariants.
It does not silently replace, embellish, reinterpret, or contradict the
agent's semantic judgment.

When semantic authorship is unavailable or invalid, Core Memory durably records
the turn as `pending_semantic`, excludes it from semantic retrieval, returns a
degraded receipt, and permits a later agent-authored retry. It does not
fabricate a canonical `context` bead that appears semantically complete.

### 2.1 Meaning of “agent-led”

Agent-led does not mean unvalidated model output. It means:

1. the agent proposes semantic meaning through a typed contract;
2. Core Memory validates shape, grounding, cardinality, and authority;
3. Core Memory persists valid agent-authored meaning without field loss;
4. deterministic code may reject, quarantine, downgrade, schedule, index, or
   shortlist, but may not invent semantic truth;
5. any later repair or enrichment that changes meaning is another attributed
   agent judgment.

The authoring agent is normally the primary host agent that has the complete
turn context. A separately invoked semantic agent is acceptable when the host
cannot author inline, provided that it receives the same typed contract and its
authorship is explicit. A heuristic classifier is not an agent judgment.

---

## 3. Problem statement

Weak beads and missing causal edges in a hosted deployment are expected under the current
defaults. The live write path currently permits this sequence:

```text
raw finalized turn
  -> optional narrow internal judge
  -> deterministic fallback bead
  -> lossy creation-row normalizer
  -> durable queued enrichment
  -> canonical bead creation during queue drain
  -> chronology-led association candidates
  -> optional association judgment
```

That sequence inverts the intended architecture. Semantic authorship is
optional, while deterministic fallback and field mutation are always
available.

### 3.1 Confirmed failure points

| Failure | Current behavior | Consequence |
|---|---|---|
| Authorship is not publicly typed | MCP `capture` cannot carry authored updates; HTTP and Python tunnel them through untyped metadata | The primary agent is not naturally asked to fill the canonical schema |
| Agent authorship is opt-in | Default mode permits fallback when authored updates are absent or invalid | Engine-created semantics become the normal path |
| Creation normalization is lossy | `_normalize_creation_rows(...)` copies about 23 of 147 `Bead` fields | Retrieval fields, keys, claims, type-specific data, and authorship values disappear |
| Schema descriptions disagree | The judge emits `state_change` as text while the normalizer retains only a dictionary | A compliant judge loses its own output |
| Retrieval eligibility is forced | Schema and store layers derive eligibility from bead type/title | An agent's explicit `false` becomes `true`; thin beads look rich |
| Judge fallback touches authored rows | Missing fields are back-filled and fallback tags are added to every row when enabled | Primary and repair authorship become indistinguishable |
| Exactly-one is not enforced | Default runtime validation receives no maximum policy and contract advertises `exactly_one=false` | A finalized turn may create multiple current-turn beads |
| Flush barrier checks the wrong fact | It checks a mechanical memory-pass marker, not canonical bead existence | Flush and receipts can advance before semantic commitment |
| Receipt discards semantic result | HTTP returns broad acceptance but omits bead, gate, fallback, and association status | Hosts cannot detect degraded writes |
| Association inputs are narrow | Candidate generation is mostly temporal and judge context omits causal evidence | `follows` dominates while causal relations remain undiscovered |
| Heuristics mutate semantic state | Promotion classification and missing-relationship preview can write decisions | Deterministic code authors meaning instead of advising |

### 3.2 Why this matters

The problem is not merely sparse records. It damages four product properties:

1. **Recall quality:** useful retrieval titles, facts, claims, and keys never
   reach storage.
2. **Causal quality:** the association agent cannot judge relationships it is
   never shown or for which it lacks causal evidence.
3. **Trust:** receipts say a write succeeded without proving that semantic
   memory exists.
4. **Authorship integrity:** operators cannot tell which fields were authored
   by the primary agent, a repair agent, or deterministic fallback code.

---

## 4. Goals

1. Make agent-authored semantic writes the default and required per-turn path.
2. Expose the existing authored-update shape as a typed field on every
   canonical write surface.
3. Inject one consistent bead-authoring contract into every supported authoring
   adapter.
4. Preserve all valid agent-authored fields through canonical persistence.
5. Enforce exactly one canonical current-turn bead attempt per finalized turn,
   while preserving bounded, explicitly derived companion memories.
6. Preserve explicit `retrieval_eligible=false` and allow only explained
   monotonic downgrades from `true` to `false`.
7. Keep the durable side-effect queue while making semantic completion and
   flush barriers truthful.
8. Ensure all canonical semantic associations and promotion decisions are
   agent-issued.
9. Improve association judgment by widening context immediately and broadening
   candidate selection subsequently.
10. Make degraded, pending, repaired, and fully agent-authored writes visibly
    distinct in storage, receipts, metrics, and operator tooling.
11. Provide a safe append-only hosted-deployment backfill path that preserves source
    anchors and provenance.

---

## 5. Non-goals

1. Do not replace the durable side-effect queue with a new job system.
2. Do not create a second bead schema when the canonical `Bead` model and
   existing authored-update validator can be extended.
3. Do not make every turn artificially rich.
4. Do not require every bead to contain claims, associations, causal rationale,
   or promotion.
5. Do not let an agent directly mutate `.beads`, `.turns`, indexes, rolling
   windows, archives, or graph backends.
6. Do not make PydanticAI, OpenClaw, or another integration the memory
   authority.
7. Do not make semantic indexing synchronous with the critical write path.
8. Do not rewrite historical evidence anchors or erase their original
   provenance.
9. Do not solve Dreamer, SOUL, or general semantic task orchestration in this
   PRD.
10. Do not treat deterministic candidate selection as permission to write a
    semantic relationship.

---

## 6. Existing assets to reuse

This work is an exposure and integrity project, not a greenfield schema
project.

| Existing asset | Role in target design |
|---|---|
| `core_memory/integrations/bead_authoring.py` | Human- and adapter-readable agent authorship guidance |
| `core_memory/runtime/passes/agent_authored_contract.py` | Existing strict authored-update validation to be migrated onto the schema-owned contract |
| `metadata.crawler_updates` | Existing compatibility ingress for authored updates |
| `core_memory/persistence/store_add_bead_ops.py` | Rich canonical persistence entry point |
| `core_memory/runtime/queue/side_effect_queue.py` | Durable, locked, idempotent deferred-work queue |
| `decide_promotion_bulk_for_store(...)` | Application path for agent-issued promotion decisions |
| association coverage and judge paths | Foundation for deterministic pair shortlisting plus agent judgment |
| canonical event and turn records | Never-forget source for pending semantic retry |

The existing internal payload name `crawler_updates` remains the v1 public
field to minimize migration cost. Renaming it to a broader term such as
`agent_memory_write` is deferred until after this contract is stable.

The canonical `AgentAuthoredUpdatesV1` type and generated JSON schema must live
under `core_memory/schema/`. Runtime validation imports that contract downward;
integrations consume it through the public package surface. This placement
prevents `schema/` from importing runtime and avoids a second handwritten bead
schema.

---

## 7. Semantic ownership model

### 7.1 Agent-owned fields and decisions

The runtime must preserve valid agent values for:

- bead type;
- title, summary, and detail;
- scope, authority, confidence, uncertainty, and tags;
- retrieval eligibility, retrieval title, and retrieval facts;
- entities, topics, and semantic key arrays;
- `because`, supporting facts, evidence references, and state change;
- effective, observed, and source-attribution semantics supplied by the turn;
- claims and claim updates;
- type-specific fields such as goal criteria, outcome result, incident
  severity, hypothesis state, document metadata, and operational record data;
- semantic association relation, direction, rationale, confidence, and
  evidence;
- promotion judgment: `promoted`, `candidate`, or `null`, plus reason.

The runtime may reject invalid values. It must not silently replace them with a
different semantic value.

### 7.2 Runtime-owned fields and mechanics

Core Memory owns:

- canonical bead ID and event ID;
- canonical `created_at` and write timestamps;
- tenant, root, session, and finalized-turn attachment;
- idempotency and replay keys;
- canonical turn record persistence;
- turn ordering and structural continuity linkage;
- append-only event and audit records;
- queue leases, retries, and drain scheduling;
- persistence status and semantic completion status;
- promotion bookkeeping timestamps and locks after applying an agent decision;
- recall counts, index state, graph projection state, and health metrics;
- flush, rolling-window maintenance, archive compaction, and checkpoints.

Runtime-generated structural values must be recorded separately from semantic
authorship provenance.

### 7.3 Deterministic logic that is allowed

Deterministic code may:

- generate IDs and timestamps;
- validate types, required fields, cardinality, and value ranges;
- deduplicate and enforce idempotency;
- attach canonical session and turn references;
- persist and queue work;
- trigger session flush and maintenance;
- compute hashes and indexes;
- apply size and safety limits;
- downgrade an invalid retrieval-eligibility claim with an explicit reason;
- shortlist bead pairs for association judgment;
- preserve chronology through linkage fields;
- compute advisory scores and recommendations;
- quarantine or mark a write pending.

### 7.4 Deterministic logic that is prohibited

Deterministic code must not canonically author:

- a bead's semantic type, title, summary, or rationale;
- claims or claim updates;
- semantic retrieval framing;
- a semantic relationship or its direction;
- promotion state;
- a causal explanation;
- inferred state change;
- missing agent fields merely to make a write appear complete.

Chronological continuity belongs in `prev_bead_id`, `next_bead_id`, turn order,
and source references. A deterministic `follows` association must not be used as
a substitute for agent-judged semantic graph structure.

---

## 8. Functional requirements

### FR-1: Typed authorship field on every write surface

The following surfaces must expose `crawler_updates` as an explicit typed,
optional-at-transport but required-by-default-at-runtime field and
`authoring_mode` as `inline|delegated`:

- Python `emit_turn_finalized(...)`;
- Python `process_turn_finalized(...)`;
- MCP `write_turn_finalized`;
- MCP `capture`;
- HTTP `TurnFinalizedRequest`;
- supported adapter hooks for PydanticAI, OpenClaw, LangChain, CrewAI, and
  SpringAI where applicable.

These fields must not rely on arbitrary `metadata` tunneling. During migration,
`metadata.crawler_updates` remains a compatibility alias. If both are present,
the top-level typed field wins and a warning is recorded.

Passive post-hoc adapters that have no live primary agent, including the hosted
OpenClaw capture bridge, must request `authoring_mode=delegated`. Core Memory
then invokes the same full-schema semantic authoring task and records
`authorship.source=delegated_semantic_agent`. They must not request or depend on
the narrow legacy bead-field judge.

MCP schemas with `additionalProperties: false` must declare the field
explicitly.

### FR-2: Authoring contract injection

Every adapter that claims agent-authored support must inject
`BEAD_AUTHORING_SPEC` or its machine-readable equivalent into the primary
agent's final-turn workflow, or explicitly request the full-schema delegated
semantic author when no live primary-agent hook exists.

The spec and contract snapshot must be generated from, or tested against, the
canonical schema so that required fields and field types cannot drift.

The injected contract must say:

- attempt exactly one `creation_role=current_turn` bead for every finalized
  top-level turn;
- optionally author at most two `creation_role=derived` companion beads, each
  grounded in and linked to `$current_turn`;
- use a thin, non-retrieval bead when the turn contains little durable meaning;
- populate optional fields only when grounded;
- use visible prior beads for justified semantic associations;
- return an explicit promotion judgment for visible session beads when the
  adapter supports inline promotion review;
- never invent facts, dates, evidence, claims, or associations.

### FR-3: One canonical current-turn bead with bounded derived companions

`validate_agent_authored_updates(...)` must require exactly one
`beads_create` row with `creation_role=current_turn`. That row must include the
finalized turn in `source_turn_ids`.

The same write may contain zero to two additional rows with
`creation_role=derived`. Every derived row must include
`derived_from_bead_ids=["$current_turn"]`; the runtime persists the primary row
first, replaces `$current_turn` with its canonical bead ID, and then persists
the derived rows. Derived-row failure is visible in the receipt but does not
erase an already committed primary bead.

This cardinality rule applies without requiring a `SidecarPolicy` instance.
Runtime defaults must not pass `max_create_per_turn=None` in a way that disables
the contract. It preserves the one-canonical-turn-bead invariant without
discarding legitimate multi-memory turns such as a decision that also yields a
lesson.

### FR-4: Lossless semantic persistence

`_normalize_creation_rows(...)` must no longer enumerate a narrow semantic
field whitelist.

It must:

1. accept fields defined by the canonical `Bead` schema;
2. preserve every valid agent-owned field;
3. apply an explicit runtime-owned overlay only for fields listed in §7.2;
4. never store unknown authored fields: hard mode rejects them, while warn mode
   drops them and lists every dropped field in the receipt;
5. never silently discard a known field;
6. record validation warnings in the write receipt and audit event.

Round-trip parity must hold from the typed public payload through persisted
canonical bead state.

### FR-5: Consistent field types

`Bead`, `BEAD_AUTHORING_SPEC`, bead-judge output, authored-update validation,
normalization, and persistence must agree on field shapes.

For `state_change`:

- the canonical v1 storage shape is an object such as
  `{"from": "proposal", "to": "approved", "description": "..."}`;
- a legacy string is accepted at ingress and losslessly normalized to
  `{"description": "<text>"}`;
- empty values normalize to `null`;
- no compliant string value may be silently replaced with `null`.

Equivalent compatibility tests are required for `because`, claims, claim
updates, temporal fields, and type-specific fields.

### FR-6: Retrieval eligibility is agent-authored and downgrade-only

An explicit `retrieval_eligible=false` must survive schema normalization and
store persistence. The field is required by hard contract validation; only
legacy and warn-mode compatibility normalization may default a missing value to
false.

Core Memory may downgrade `true` to `false` when the payload fails the quality
contract. A downgrade must include a machine-readable reason and must not alter
the underlying authored fields.

Eligibility enforcement is phased to avoid a rollout window in which the
runtime requires fields that the injected authoring spec does not yet request:

1. the lossless-persistence slice preserves false, removes deterministic
   upgrades, and may downgrade true only for a generic title;
2. after typed contract/spec injection ships, the full quality contract for
   true becomes:

   - a non-generic title;
   - a useful `retrieval_title`;
   - at least one concrete `retrieval_fact`;
   - at least one grounded quality signal such as `because`, supporting facts,
     evidence refs, state change, or supersession.

Storage/index participation is a separate concern. A bead may be stored and
indexed while remaining ineligible for normal semantic recall.

### FR-7: Claims remain agent-authored

Claims and claim updates present in the authored bead must persist regardless
of whether automatic claim extraction is enabled.

`claim_layer_enabled` and `claim_extraction_mode` govern optional extraction or
review behavior; they must not govern preservation of valid authored claims.

Deterministic extraction may emit an advisory candidate in an explicitly
attributed repair path. It may not silently create canonical claim truth.

### FR-8: Promotion is an agent decision

The per-turn heuristic promotion pass must stop mutating canonical promotion
state on its own.

Heuristics may produce a recommendation containing score, proposed state, and
reasons. An agent-issued decision must be applied through the existing
promotion decision surface.

Core Memory retains authority over:

- whether the transition is structurally allowed;
- promotion locking and timestamps;
- append-only audit;
- later governed rebalance rules explicitly defined by policy.

### FR-9: Delegated authoring and repair cannot blur primary authorship

`_maybe_apply_judge_fallback(...)` must not run over a valid, complete
agent-authored row.

The canonical delegated task is `turn_memory_authoring`. It must emit the full
`agent_authored_updates.v1` contract, including retrieval title/facts, semantic
keys, claims, type-specific fields, derived rows, associations, and promotion
reviews. The narrow bead-field judge becomes a compatibility alias for one
deprecation window; it is not a separate canonical schema.

Delegated output is legitimate agent authorship only when the receipt records
`authorship.source=delegated_semantic_agent` plus model, prompt, schema,
grounding, and semantic-task receipt provenance.

The same task may run in repair mode only when:

- the write has entered an explicit repair flow;
- the repair mode is enabled by policy;
- the same canonical typed contract is used;
- primary and repair authorship are recorded separately;
- the receipt reports `repair_used=true` and lists repaired fields.

Repair output must never be labeled as wholly primary-agent-authored. Repaired
fields require field-level provenance.

### FR-10: Semantic associations are agent-judged

Every canonical association row must contain:

- source and target bead IDs;
- agent-selected relationship and direction;
- grounded `reason_text`;
- confidence;
- judge identity or profile;
- evidence or grounding references;
- prompt/rubric/schema version where available.

When an authored row omits `relationship`, the write must be rejected,
quarantined, or returned for repair. The deterministic preview classifier may
suggest a relationship but may not write it canonically.

Direct association-write helpers must be split or constrained so callers
cannot bypass the boundary. Structural chronology and provenance should use
bead linkage/source fields. Any retained structural graph projection must be
stored in a separately typed edge class and excluded from semantic causal
metrics and recall.

### FR-11: Association judging receives causal context

The immediate association-context projection must include, when present:

- title, summary, detail, and type;
- `because` and supporting facts;
- state change and retrieval facts;
- entities, topics, and semantic keys;
- claims and claim updates relevant to the pair;
- temporal validity and supersession;
- authority, confidence, source refs, and attribution.

The next candidate-generation iteration must deterministically shortlist pairs
using a combination of:

- explicit bead references;
- entity and semantic-key overlap;
- semantic similarity;
- temporal proximity;
- claim slot overlap or conflict;
- goal/decision/outcome continuity;
- source derivation and revision references.

The agent, not the shortlist, selects `relationship`, direction, or `no_link`.
The agent may propose additional pairs visible in its bounded context.

### FR-12: Pending semantic degradation path

If authored updates are missing, invalid, or unavailable under hard mode, Core
Memory must:

1. persist the canonical finalized-turn record;
2. persist a separate pending-semantic record keyed by session and turn;
3. exclude that record from normal semantic retrieval and promotion;
4. avoid emitting a canonical semantic bead;
5. enqueue or expose a retry operation;
6. return `accepted=true`, `semantic_status=pending`, `ok=false`, and a stable
   error code;
7. preserve sufficient context for an authorized agent to retry idempotently.

The current `blocked_wrote_stub` behavior may be reused as implementation
scaffolding, but the stub must not masquerade as a normal `context` bead.

Only the latest finalized turn participates in the flush barrier. If it is
pending, a forced flush requires an explicit operator override and writes an
audit record. Older pending turns do not wedge compaction after a newer turn
commits; they remain visible through pending-semantic age metrics, doctor
alarms, and retry/backfill surfaces.

### FR-13: Keep the queue; fix the semantic barrier

The durable side-effect queue remains the mechanism for deferred enrichment.

The flush barrier and semantic receipt must not rely on
`mark_memory_pass(..., "done")`. For the latest finalized turn only, the
authoritative check is:

```text
canonical current-turn bead exists for (session_id, turn_id)
```

or, for an explicitly waived degraded turn:

```text
pending-semantic record exists and waiver audit exists
```

Queue drain failure must leave the durable item available for retry. Logs and
docstrings must not claim that the bead is already persisted when creation is
still queued.

Pending-semantic observability must report total pending count, each pending
turn's age, and oldest pending age. `doctor` warns at five minutes and reports
critical at sixty minutes. This monitoring applies to older pending turns that
no longer block the latest-turn-only flush barrier.

### FR-14: Truthful semantic receipt

Every canonical write surface must return the same semantic receipt shape.

Minimum fields:

```json
{
  "accepted": true,
  "ok": true,
  "retryable": false,
  "session_id": "s1",
  "turn_id": "t1",
  "event_id": "evt-...",
  "bead_id": "bead-...",
  "semantic_status": "committed",
  "authorship": {
    "source": "primary_agent",
    "schema_version": "agent_authored_updates.v1",
    "used_fallback": false,
    "repair_used": false,
    "repaired_fields": []
  },
  "validation": {
    "valid": true,
    "warnings": [],
    "downgrades": []
  },
  "associations": {
    "status": "complete",
    "candidates": 4,
    "judged": 4,
    "written": 2,
    "pending": 0
  },
  "queue": {
    "status": "drained",
    "item_id": "se-..."
  }
}
```

Allowed `semantic_status` values:

- `committed`;
- `pending`;
- `repair_required`;
- `waived`;
- `failed`.

Allowed `associations.status` values:

- `complete` — linked or agent-judged `no_link`/no-supported-links;
- `pending` — deferred work has not reached the judge;
- `pending_judge` — candidates exist but no judge decision is available;
- `failed` — judge, quarantine, or application failure;
- `skipped` — the bead was ineligible for association coverage.

Association work may remain pending after the current-turn bead is committed,
but the receipt must say so. A host must be able to distinguish bead commitment
from complete causal enrichment.

---

## 9. Canonical authored-update contract

The existing `crawler_updates` shape is formalized as
`agent_authored_updates.v1`. Its canonical typed definition and generated JSON
schema live in `core_memory/schema/`; the runtime validator, agent instructions,
semantic-task author, and adapter schemas consume that single definition.

```python
class AgentAuthoredUpdates(TypedDict):
    schema_version: Literal["agent_authored_updates.v1"]
    beads_create: list[AgentAuthoredBead]  # one current_turn, zero-to-two derived
    associations: list[AgentAuthoredAssociation]
    reviewed_beads: list[PromotionReview]
```

`claims` and `claim_updates` remain embedded on `AgentAuthoredBead` because they
are semantic state authored for the current turn.

### 9.1 Required bead fields

Every bead must include:

- `creation_role` as `current_turn|derived`;
- `type`;
- `title`;
- `summary` as `list[str]`;
- `entities` as `list[str]`, which may be empty for a genuinely entity-free
  thin turn;
- `retrieval_eligible`;
- `source_turn_ids`, attached or verified by the runtime.

Exactly one row must use `creation_role=current_turn`. A derived row must use
`creation_role=derived` and `derived_from_bead_ids=["$current_turn"]`.

Causal bead types require grounded `because`. Type-specific requirements in
`BEAD_AUTHORING_SPEC` remain enforced.

The existing validator currently requires a non-empty `entities` list. This
must be corrected: an empty typed list is valid when no grounded entity exists.
The guardrail must never encourage invention to satisfy non-emptiness.

### 9.2 Optional means “fill when justified”

Optional fields are not low-priority fields. They are conditionally applicable
fields. Adapters should instruct agents to evaluate them every turn and populate
them when grounded.

The receipt may include field-coverage diagnostics, but field count must never
be used as the sole quality score.

### 9.3 Authorship provenance

Each semantic write audit record must include:

- authorship source: primary agent, delegated semantic agent, repair agent, or
  legacy deterministic mode;
- agent/model profile where available;
- contract version;
- prompt/spec version;
- input grounding hash;
- validation result;
- fields repaired or downgraded;
- legacy/fallback mode, if any.

Field-level provenance is required for repaired fields. Whole-record provenance
is sufficient when no repair occurred.

---

## 10. Write lifecycle

```text
finalized turn accepted
  -> raw/canonical turn record persisted
  -> typed authored updates validated
      -> valid: enqueue/apply canonical semantic write
      -> invalid: pending_semantic + retryable receipt
  -> canonical bead existence confirmed
  -> semantic receipt = committed
  -> deferred association/claim/promotion review work continues
  -> flush barrier checks canonical bead or explicit waiver
```

### 10.1 State definitions

| State | Meaning | Retrieval allowed | Flush allowed |
|---|---|---:|---:|
| `accepted` | Turn event received; semantic validation not complete | No | No |
| `pending` | Turn durable; no valid canonical semantic bead | No | No, except audited override |
| `repair_required` | Authored payload failed validation and is awaiting agent repair | No | No, except audited override |
| `committed` | Exactly one canonical current-turn bead exists | According to bead eligibility | Yes |
| `waived` | Operator explicitly allowed a pending turn to pass flush | No | Yes |
| `failed` | Turn or persistence could not be durably recorded | No | No |

Association enrichment has a separate state and does not redefine semantic
bead commitment.

Flush checks only the latest finalized turn. Older `pending` or
`repair_required` turns remain operational debt surfaced by age metrics and
doctor alarms, but they do not block a later committed turn from anchoring
flush.

---

## 11. Implementation plan

### Slice 1 — Lossless persistence and turn-time derivation

This is the first and highest-leverage slice.

1. Replace `_normalize_creation_rows(...)` with schema-driven copying.
2. Define the runtime-owned overlay explicitly.
3. Preserve `scope`, `authority`, `confidence`, retrieval fields, semantic keys,
   claims, temporal/source fields, and type-specific data.
4. Add `creation_role`, primary-first persistence, `$current_turn` resolution,
   and support for at most two derived rows.
5. Normalize legacy text `state_change` into its canonical object form.
6. Preserve explicit `retrieval_eligible=false`, remove deterministic upgrades,
   and use a generic title as the only downgrade cause in this slice.
7. In warn mode, drop unknown fields and return their names; in hard mode,
   reject them. Unknown fields are never stored.
8. Add all-field round-trip, multi-memory-turn, and contradiction tests.

**Exit condition:** a valid rich agent-authored bead reaches storage without
known field loss or unexplained value mutation, and a decision plus derived
lesson can be committed in one typed turn write.

**Implementation:** Shipped in #404. Canonical writes now use schema-derived
field ownership and a runtime overlay, preserve authored eligibility and
normalized state changes, persist the current-turn bead before up to two derived
companions, and report independent derived-write failures.

### Slice 2 — Typed ingress and delegated semantic authorship

1. Put `AgentAuthoredUpdatesV1` and its generated JSON schema in `schema/`.
2. Add typed top-level `crawler_updates` and `authoring_mode=inline|delegated`
   to Python, MCP, HTTP, `TurnEnvelope`, and adapter surfaces.
3. Include both fields in the envelope hash. Document that upgraded retries may
   appear as superseded envelope hashes while memory-pass identity remains
   `(session_id, turn_id)`.
4. Add `crawler_updates` to MCP `capture` despite
   `additionalProperties: false` and keep `metadata.crawler_updates` as a
   deprecated compatibility alias.
5. Introduce `turn_memory_authoring` in the semantic-task runtime. Upgrade the
   bead-judge compatibility alias to emit the full authored-update contract.
6. Record delegated authorship and semantic-task provenance explicitly.
7. Inject the same full contract into inline-capable MCP, PydanticAI, and
   OpenClaw flows; passive adapters explicitly request delegated authorship.
8. Change the hosted capture bridge from `bead_judge=llm` to
   `authoring_mode=delegated`, landing backend support and the bridge switch
   together so no structural-fallback regression window exists.
9. Activate the full retrieval-framing quality bar after the new spec is being
   supplied.
10. Add integration tests for every supported adapter.

**Exit condition:** an authoring agent can discover, populate, submit, and
receive validation for the same typed contract on every canonical ingress, and
the hosted capture path receives full-schema delegated authorship.

### Slice 3 — Semantic state, truthful receipts, and flush barrier

1. Add separate semantic-write state and append-only status history keyed by
   session and turn.
2. Mark committed only when canonical current-turn bead lookup succeeds;
   report derived-row failures separately.
3. Standardize `memory.turn_finalized_receipt.v2` across processed Python, MCP,
   and HTTP surfaces.
4. Keep the event-only Python emit facade for one compatibility window and add
   a canonical processed Python write facade returning the v2 receipt.
5. Map association internals into the five public statuses in FR-14.
6. Change flush to inspect the latest finalized turn only and require its
   canonical bead or an audited waiver.
7. Add pending count, per-turn age, oldest age, and doctor warning/critical
   thresholds.
8. Preserve durable queue retry behavior and correct inaccurate queued-path
   logs and docstrings.

**Exit condition:** callers and flush agree on whether the latest current-turn
bead exists, and older pending turns are visible without globally wedging
compaction.

### Slice 4 — Hard authorship and explicit degradation

1. Enforce exactly one `current_turn` row and zero-to-two `derived` rows
   independent of optional policy objects.
2. Allow an empty typed entity array when the turn contains no grounded entity.
3. Change the final default of `CORE_MEMORY_AGENT_AUTHORED_MODE` from `warn` to
   `hard` only after the release gates pass.
4. Replace canonical fallback `context` beads with pending-semantic records.
5. Use the full `turn_memory_authoring` task for explicit repair mode and
   record repaired fields separately from primary authorship.
6. Ensure valid primary-agent rows never pass through fallback repair.
7. Preserve authored claims regardless of extraction flags.
8. Make only the latest pending turn block normal flush; older pending turns
   rely on alarms and retry/backfill.

**Exit condition:** no normal production turn can create deterministic canonical
semantics when the authored contract is missing or invalid.

### Slice 5 — Remove deterministic semantic authority

1. Convert heuristic promotion to shadow recommendations and route canonical
   application through the agent-decision surface.
2. Require at least 20 completed sessions and 100 promotion-eligible beads,
   99% agent-decision coverage, review of every divergence, and zero unresolved
   high-severity heuristic-only promotions before disabling heuristic writes.
3. Treat model-backed typed claim extraction as delegated authorship and keep
   heuristic extraction advisory-only.
4. Stop preview-classifier relationships from becoming canonical directly.
5. Restrict direct relationship append helpers by edge class and provenance.
6. Add `scripts/architecture_guards_baseline.json` entries enumerating every
   sanctioned deterministic writer, its permitted record/edge class, rationale,
   and provenance requirement. Initial sanctioned writers cover session
   boundaries, flush checkpoints, source-attributed external evidence anchors,
   pending-semantic state, and explicit structural-field projections.
7. Make the architecture guard fail on unlisted deterministic semantic writers.
8. Add a candidate-only replacement for `backfill-causal-links`. During one
   documented compatibility window, accept legacy `--apply` only to emit a
   deprecation warning and telemetry; it must not write semantic links.

**Exit condition:** canonical promotion, claims, and semantic relationships have
agent-issued provenance, and all deterministic writers are explicitly
classified.

### Slice 6 — Causal association quality and compatibility cutover

1. Widen `_bead_context(...)` with causal, retrieval, claim, and temporal
   fields.
2. Add semantic/entity/claim/goal candidate pair shortlisting.
3. Let the agent judge relation, direction, or `no_link`.
4. Add association-judge readiness checks and pending-age metrics.
5. Separate structural continuity from semantic causal graph metrics.
6. During the compatibility-ledger deprecation window, treat
   `backfill-causal-links --apply` as candidate-only and emit migration
   guidance. Reject the argument after that window.

**Exit condition:** causal fixtures can produce justified non-temporal
relationships, and the judge is never asked to infer them without the evidence
fields required to do so.

### Slice 7 — Governed reauthoring and hosted-deployment backfill

**Implementation status:** Engine implementation complete; hosted copied-tenant
and live-tenant execution remains an operator rollout step. The governed maintenance surface is
dry-run-first, copied-tenant-gated for live apply, append-only for legacy source
beads and evidence anchors, and cohort-aware in its receipts and report.

1. Add dry-run-first `reauthor_memory` and `retry_pending_semantic` governed
   maintenance actions using the full delegated authoring task.
2. Preserve original external evidence anchors.
3. Commit pending turns through the canonical current-turn path.
4. Append richer interpretations of existing thin beads as derived or revision
   beads rather than mutating them.
5. Rerun agent-judged causal association coverage only after semantic writes
   commit.
6. Record backfill authorship, contract version, source refs, and timestamps.
7. Compare separately cohort-ed legacy, v1-authored, and backfilled bead
   richness, retrieval, claims/keys, and causal-edge metrics.

**Exit condition:** the hosted deployment no longer depends on legacy deterministic semantics,
and provenance remains append-only.

---

## 12. Acceptance criteria

### 12.1 Authorship and persistence

1. Every finalized normal-path turn submits exactly one authored
   `current_turn` bead attempt and no more than two explicitly derived rows.
2. A valid agent-authored payload is persisted without loss of any known
   agent-owned field.
3. Runtime-owned fields are applied without overwriting semantic values.
4. A known field may not disappear without a validation error or explicit
   downgrade in the receipt.
5. An explicit `retrieval_eligible=false` remains false.
6. A `true` eligibility value that fails quality validation becomes false with
   an explicit reason.
7. Authored claims persist when automatic claim extraction is off.
8. A text `state_change` remains represented after persistence.
9. Unknown fields are never stored: hard mode rejects and warn mode reports
   every dropped field.
10. A decision plus derived lesson can be committed atomically enough that the
    primary remains valid if the derived row later fails.

### 12.2 Degradation and queue behavior

1. Missing or invalid authored updates produce a pending-semantic record, not a
   canonical context bead.
2. A queue-drain failure leaves a durable retryable item.
3. The receipt reports pending until canonical bead existence is confirmed.
4. Normal flush refuses a latest turn with no canonical bead.
5. An audited waiver permits flush without making the pending record
   retrieval-eligible.
6. Queue replay is idempotent and produces at most one current-turn bead.
7. An older pending turn does not block flush after a newer turn commits.
8. Pending count and age remain visible, with doctor warning at five minutes
   and critical status at sixty minutes.

### 12.3 Associations and promotion

1. No canonical semantic relationship is written without agent-issued
   relation, direction, rationale, and confidence.
2. A missing relationship is rejected or returned for repair, not filled and
   written by the preview classifier.
3. Judge context contains the causal fields specified in FR-11.
4. A decision-to-outcome fixture can produce `led_to` or the justified
   canonical equivalent, not only `follows`.
5. `no_link` remains a valid agent result.
6. Heuristic promotion cannot mutate canonical promotion state without an
   agent decision.

### 12.4 Receipts and observability

1. Python, MCP, and HTTP expose equivalent semantic receipt fields.
2. Every committed receipt includes `bead_id` and authorship source.
3. Every pending receipt includes a stable error code and retryability.
4. Repair and downgrade fields are visible.
5. Association pending/completion status is visible separately from bead
   commitment.
6. Association status is one of `complete`, `pending`, `pending_judge`,
   `failed`, or `skipped`.

---

## 13. Test plan

### 13.1 Unit tests

- validator requires exactly one current-turn row and permits zero-to-two
  derived rows without a policy object;
- validator allows an empty typed `entities` list when grounded entities do not
  exist;
- every canonical bead field survives creation normalization;
- runtime-owned overlay cannot overwrite agent-owned semantics;
- text and object `state_change` inputs normalize without loss;
- explicit retrieval ineligibility survives both normalization layers;
- retrieval eligibility downgrade emits a reason;
- primary-agent rows bypass judge fallback;
- unknown fields hard-reject or warn-drop without persistence;
- preview classifier cannot write a canonical semantic association;
- heuristic promotion returns advice without applying state.

### 13.2 Contract and property tests

- derive a field inventory from the `Bead` dataclass and fail when a new field
  lacks an ownership classification;
- generate valid values for all canonical fields and assert round-trip parity;
- compare `BEAD_AUTHORING_SPEC`, contract snapshot, judge schema, and canonical
  model field types;
- fail if a known semantic field is accepted at ingress but absent after
  persistence;
- fail if public adapter schemas omit `crawler_updates`.

### 13.3 Integration tests

- Python, MCP write, MCP capture, and HTTP persist the same authored payload to
  equivalent canonical state;
- queue drain succeeds inline;
- queue drain fails, maintenance retries, and exactly one current-turn bead
  appears without duplicate derived rows;
- flush before semantic commitment is retryable;
- a newer committed turn releases the latest-turn-only barrier while older
  pending age remains visible;
- pending-semantic retry becomes committed idempotently;
- claim extraction off does not delete authored claims;
- association judge unavailable produces explicit pending status;
- full causal fixture yields grounded semantic edges.

### 13.4 Regression fixtures

Include fixtures for:

- a thin conversational turn;
- a decision with rationale and constraints;
- a goal with success criteria;
- an outcome linked to a decision;
- a correction or reversal;
- a claim and later claim supersession;
- a document/evidence anchor plus derived semantic assertion;
- a failed association-judge invocation;
- a legacy string `state_change`;
- a rich all-fields bead;
- a decision with a derived lesson in the same authored update.

---

## 14. Metrics and operational readiness

### 14.1 Required metrics

- finalized turns received;
- valid primary-agent writes;
- delegated-agent writes;
- pending-semantic writes;
- pending-semantic age per turn, oldest pending age, and counts above five- and
  sixty-minute thresholds;
- repair-agent writes;
- legacy deterministic writes;
- schema validation failures by field and adapter;
- known fields dropped or downgraded;
- retrieval eligibility true/false and downgrade reasons;
- retrieval title/fact coverage;
- claim and semantic-key coverage;
- canonical bead commit latency;
- queued turn-enrichment age;
- association candidates, judged pairs, `no_link`, written edges, and pending
  age;
- semantic relationship distribution, excluding structural linkage;
- promotion decisions by authorship source;
- promotion shadow/agent divergence counts and review disposition.

### 14.2 Release gates

The hard-default rollout cannot complete until:

1. supported adapters submit typed authorship on at least 99% of eligible
   finalized turns in the rollout environment;
2. unknown silent field loss is zero;
3. deterministic canonical semantic writes are zero outside an explicitly
   enabled legacy-emergency mode;
4. committed receipts always resolve to an existing canonical bead;
5. pending writes are visible in `doctor`/health output;
6. association-judge absence is visible and does not masquerade as complete
   enrichment;
7. the hosted capture bridge successfully receives full-schema delegated
   authorship rather than narrow judge or structural fallback output.

The 99% adapter target measures successful contract submission, not richness.
Thin valid beads remain acceptable.

---

## 15. Rollout and compatibility

### 15.1 Feature posture

Final defaults:

- agent-authored mode: `hard`;
- legacy deterministic bead fallback: off;
- judge repair: off unless explicitly configured;
- automatic claim extraction: independent of authored-claim preservation;
- association judge readiness: required when semantic edge generation is
  enabled.

An emergency legacy mode may remain temporarily, but every use must:

- emit a high-visibility metric and receipt warning;
- mark authorship as `legacy_deterministic`;
- set retrieval eligibility false unless later reviewed by an agent;
- be excluded from release-quality semantic metrics.

### 15.2 Backward compatibility

- `metadata.crawler_updates` remains readable for one deprecation window;
- top-level typed `crawler_updates` takes precedence;
- the legacy `bead_judge=llm` directive aliases full delegated
  `turn_memory_authoring` for one deprecation window;
- legacy `state_change` strings are accepted and normalized;
- old receipts remain parseable, but new fields are added consistently;
- existing canonical beads are not rewritten automatically;
- existing queue items remain replayable.
- adding `crawler_updates` and `authoring_mode` to the envelope hash can make an
  upgraded retry appear as a superseded envelope; idempotency and memory-pass
  claiming remain keyed by `(session_id, turn_id)`.
- existing stored beads retain their historical retrieval eligibility until
  explicitly reauthored. Quality comparisons must segment legacy
  pre-contract, `agent_authored_updates.v1`, and backfilled cohorts.
- `graph backfill-causal-links --apply` is candidate-only immediately. It
  follows the warning-and-removal schedule recorded in
  `docs/compatibility_ledger.md`; its replacement is the candidate-plus-agent-
  judge flow.

### 15.3 Hosted-deployment migration

The current hosted-deployment evidence-anchor beads remain immutable provenance records.
Backfill creates new derived semantic beads with explicit source references.

Backfill must not:

- mutate source text or attribution;
- convert every evidence anchor into retrieval-eligible memory automatically;
- generate causal edges without agent judgment;
- treat temporal adjacency as causal proof.

Backfill reporting must include:

- source anchors examined;
- derived beads proposed, validated, committed, and rejected;
- before/after retrieval-field coverage;
- before/after claims and semantic-key coverage;
- association candidates, agent judgments, written causal edges, and
  `no_link` results;
- any pending or failed items.

Before/after totals must not compare legacy historically forced eligibility
directly with new v1 policy as if they were one homogeneous population. Reports
must show each cohort separately and may include an aggregate only as a
secondary operational number.

---

## 16. Security, privacy, and authority

1. The typed authored payload must obey existing tenant/root isolation.
2. Receipts must not echo raw private turn content by default.
3. Agent prompts receive only bounded, authorized prior-bead context.
4. Association candidates must not cross tenant or authority boundaries.
5. Repair agents may not gain broader source access than the original authoring
   context without explicit policy.
6. Operator waiver and legacy-emergency mode require append-only audit.
7. Unknown fields are never stored. Hard mode rejects them; warn compatibility
   mode drops them and reports their names. Oversized payloads fail validation.

---

## 17. Performance requirements

1. Lossless schema normalization should be linear in the authored payload size.
2. Typed exposure must not add an additional model call when the primary agent
   authors inline.
3. The durable queue remains available to protect the per-turn latency target.
4. Canonical bead existence lookup used by receipts and flush must use the
   existing turn-to-bead index or an equivalent bounded lookup.
5. Association context expansion must remain bounded by candidate count and
   token budget.
6. Candidate shortlisting may be deterministic and approximate; canonical
   relationship judgment may not be.

---

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hard mode increases pending writes for adapters that do not author | Roll out typed exposure first; measure submission coverage; provide explicit pending/retry behavior |
| Rich schema increases payload size | Use conditional fields, bounded lists, and thin beads; do not demand artificial completeness |
| Schema-driven copy accidentally exposes runtime fields | Maintain a tested ownership map and runtime-owned deny/overlay set |
| Agents invent entities to satisfy validation | Allow an empty typed entity list and evaluate grounded coverage rather than non-emptiness |
| Repair agent hides primary-agent failures | Record field-level repair provenance and return it in receipts |
| Queue semantics remain confusing | Separate `accepted`, `semantic_status`, and association status; correct false comments/logs |
| Fewer heuristic edges initially reduce graph density | Prefer truthful sparse graphs; measure justified causal coverage rather than raw edge count |
| Existing clients depend on fallback context beads | Provide a deprecation window and a visible legacy-emergency mode |
| Promotion behavior changes retention | Run agent and heuristic decisions side-by-side for at least 20 completed sessions and 100 promotion-eligible beads; require 99% agent-decision coverage, review every divergence, and leave zero unresolved high-severity heuristic-only promotions before disabling writes |
| Hosted capture has no live authoring agent | Request the full `turn_memory_authoring` delegated task and land backend support with the bridge switch |
| Compatibility break in causal backfill CLI | Ledger the surface, make `--apply` candidate-only immediately with warnings and telemetry, then reject the legacy argument after the documented deprecation window |

---

## 19. Expected file surfaces

Implementation is expected to touch existing modules rather than add new flat
runtime modules:

- `core_memory/integrations/bead_authoring.py`
- `core_memory/integrations/mcp/typed_write.py`
- `core_memory/integrations/mcp/registry.py`
- `core_memory/integrations/http/server.py`
- adapter-specific finalized-turn hooks
- `core_memory/runtime/passes/agent_authored_contract.py`
- a dependency-light `AgentAuthoredUpdatesV1` contract under
  `core_memory/schema/`
- `core_memory/runtime/turn/turn_flow.py`
- `core_memory/runtime/engine.py`
- `core_memory/runtime/flush/flush_flow.py`
- `core_memory/runtime/passes/enrichment.py`
- `core_memory/association/crawler_contract.py`
- `core_memory/runtime/associations/coverage.py`
- `core_memory/schema/models.py`
- `core_memory/persistence/store_add_bead_ops.py`
- `core_memory/persistence/promotion_service.py`
- claim application/extraction boundaries
- HTTP/MCP/Python contract and end-to-end tests
- architecture guards for schema/adapter drift

Any new runtime implementation belongs in an existing sanctioned subpackage,
consistent with `CLAUDE.md`.

---

## 20. Resolved product decisions

1. **Reuse, do not rebuild:** existing authored-update validation and rich store
   support are the implementation foundation.
2. **Normalizer first:** field preservation is the first slice because every
   richer authoring improvement depends on it.
3. **Keep the queue:** durability and retries are valuable; repair the barrier
   and receipt instead of moving all enrichment synchronously.
4. **Hard is the final default:** agent authorship is required, with explicit
   pending semantics as the degradation path.
5. **Exactly one canonical current-turn bead:** thin beads handle
   low-information turns; the same typed write may include up to two explicitly
   derived companion beads linked through `$current_turn`.
6. **No forced retrieval eligibility:** false is preserved; true may only be
   downgraded with cause.
7. **No heuristic canonical semantics:** classifiers and scores may advise but
   do not author bead meaning, promotion, claims, or relationships.
8. **Sparse is acceptable:** an honest `no_link` or thin bead is better than a
   dense but weak memory graph.
9. **Append-only migration:** hosted-deployment source anchors remain intact; improved
   semantics are new attributed records.

---

## 21. Definition of done

This PRD is complete when:

1. all seven implementation slices meet their exit conditions;
2. agent-authored mode is hard by default;
3. every supported finalized-turn surface exposes the typed authored payload;
4. all known bead fields have tested ownership and round-trip behavior;
5. exactly one current-turn bead and zero-to-two explicitly derived companion
   beads are enforced;
6. pending semantics replace fabricated canonical fallback beads;
7. flush and receipts agree with canonical bead existence;
8. retrieval eligibility is never deterministically upgraded;
9. canonical semantic associations and promotion decisions have agent-issued
   provenance;
10. association agents receive causal evidence and can discover justified
    non-temporal relations;
11. hosted-deployment backfill completes without mutating original provenance;
12. `docs/status.md`, this PRD, architecture guidance, adapter contracts, and
    operator documentation describe the same shipped behavior.
