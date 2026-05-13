# Post-Demo TODO

Items identified during PydanticAI integration and demo build. These are shortcuts taken for the demo that need proper implementation.

## Cross-repo references

This TODO is the main `Core-Memory` engine-correctness list. The paired adoption/API
roadmap lives in `Core-Memory-Demo/TODO.md` on the JohnnyFiv3r/Core-Memory-Demo repo.
Keep the two files separate: this one tracks correctness and durable memory behavior;
the demo TODO tracks adoption surfaces, endpoints, packaging, and benchmark reporting.

- **#1 extracted `because` reasoning** supports Demo TODO #6 (reproducible LoCoMo /
  LongMemEval scoring). Honest causal reasoning is part of what makes benchmark recall
  defensible; do not let answer/edge quality depend on echoed user text.
- **#2 goal lifecycle** supports Demo TODO #5 (`POST /api/recall` and the public
  `recall(query, effort="low|medium|high")` verb). Public recall needs resolved goals and
  outcomes to surface as stable state, not indefinite candidates.
- **#3 canonical association relationship types** supports Demo TODO #3 (generic async
  transcript ingestion) and #6 (benchmark scoring). LoCoMo/entity-overlap or generic
  transcript heuristics should emit canonical relationships such as `associated_with`,
  `supports`, `follows`, and `precedes`; heuristic names belong in `reason_code` or
  `reason_text`.
- **#4 question classification guardrail** supports Demo TODO #7 (agent instructions in
  live integrations). Integration prompts should explicitly say questions are retrieval
  turns/context, not declarative memories to promote. Closed in code on 2026-05-13:
  question and retrieval-imperative turns are forced to `context` before LLM bead typing,
  and LLM field-judge output cannot promote those turns as precedent/decision/lesson.
- **#5 grounding hashes** and **#6 monotonic claim sequencing** support Demo TODO #5 and
  #6. The single public recall endpoint and reproducible benchmark harness need stable
  judged evidence, claim mutation, and supersede behavior independent of async job
  completion order.
- **#7 semantic indexing ergonomics** supports Demo TODO #2 (`capture` / `recall` aliases)
  and #3/#8 (async transcript ingestion across hosted demo, local demo, CLI, MCP, and
  direct-library surfaces). A successful `capture(...)` or transcript ingest should enqueue
  durable semantic deltas so users experience indexing as automatic while the queue/manifest
  remain inspectable. As of 2026-05-13, Demo TODO #8 adoption surfaces are closed; any
  remaining "recallable without manual rebuild" polish belongs here under semantic lifecycle
  ergonomics, not in the transcript-ingest surface contract.

---

## 1. Replace echo-based `because` with LLM extraction

**Status:** Closed. The live bead-field judge authors `because` as grounded free-text support for applied semantic labels/state, and the in-path OpenClaw instructions now define the field clearly for agent/adapter authorship.

**What changed:**
- `because` is explicitly defined as support for the bead's type, durability, state change, retrieval eligibility, or promotion-worthy interpretation.
- Short quoted or closely paraphrased user text is allowed when that text itself is the support.
- Guessed filler and long whole-turn dumps are rejected/normalized; weak unsupported speculation still yields empty `because`.
- The LLM bead-field judge prompt now frames `because` as support, not an anti-echo string transformation.
- `AGENT_INSTRUCTIONS.md` and `docs/integrations/openclaw/canonical_contract.md` mirror the in-path definition; the OpenClaw skill instructions and plugin skill reminder match it.

**Files:** `core_memory/policy/bead_judge.py`, `core_memory/policy/rationale.py`, `AGENT_INSTRUCTIONS.md`, `docs/integrations/openclaw/canonical_contract.md`, `docs/integrations/openclaw/core-memory-skill-instructions.md`, `plugins/openclaw-core-memory-bridge/skills/core-memory/SKILL.md`, `tests/test_rationale_extraction.py`

## 2. Goal lifecycle — resolution mechanism

**Current behavior:** Goals classify correctly and stay as `candidate` indefinitely. There is no way to resolve or close a goal.

**Problem:** When a later turn says "we finished the OAuth2 migration", nothing links that outcome to the original goal bead or transitions the goal to a resolved state.

**Fix:** Build a goal resolution pass that:
- Detects when a new `outcome` bead relates to an open `goal` bead
- Creates an association linking the outcome to the goal
- Transitions the goal status (e.g. `candidate` → `promoted` or a new `resolved` state)

This could be LLM-assisted (ask "does this turn resolve any open goals?") or heuristic (keyword/semantic matching between outcomes and goals).

**Files:** `core_memory/runtime/engine.py`, `core_memory/policy/promotion_contract.py`

## 3. Association relationship types

**Current behavior:** All associations created from `association_preview` have relationship type `shared_tag`. This is the store's quick-match heuristic.

**Problem:** The relationship types should be more descriptive — `caused_by`, `led_to`, `reinforces`, etc. The schema supports 28 relationship types but only `shared_tag` is used in practice through the PydanticAI path.

**Fix:** Either use the LLM to classify the relationship type when queuing associations, or improve the store's preview logic to infer richer relationship types from bead content.

**Files:** `core_memory/runtime/engine.py` (`_queue_preview_associations`), `core_memory/persistence/store.py` (association preview logic)

## 4. Bead type classifier — questions misclassified as precedent

**Status:** Closed. Question and retrieval-imperative turns now force `context` before LLM bead typing, and the LLM bead-field judge cannot override that guardrail with `precedent`, `decision`, or another promotable type.

**What changed:**
- Direct questions (`why/how/what...`, trailing `?`) are retrieval/context turns.
- Retrieval imperatives (`show me`, `tell me`, `remind me why`, `explain why`, `find`, `search`) are also context.
- Declarative capture imperatives (`Record that...`, `Remember that...`) are still typed by what they encode.
- Runtime regression coverage proves a turn like "Why did we decide to always benchmark representative workloads?" creates a `context` bead with empty `because` and not a promotable `precedent`.

**Files:** `core_memory/policy/bead_typing.py`, `core_memory/policy/bead_judge.py`, `core_memory/policy/rationale.py`, `tests/test_rationale_extraction.py`

## 5. Grounding hashes for judged association/claim validation

**Current behavior:** Async graph/claim validators can re-judge the same evidence slice without a durable idempotence key tying the verdict to the exact grounding evidence.

**Problem:** Re-querying the same bead/evidence slice can produce the "same data, different answer" pattern if model output varies. This makes causal edge validity and claim mutation less stable than the append-only bead store itself.

**Fix:** Store a lightweight grounding fingerprint on judged edges/claim validations, including fields such as:
- `grounding_hash`
- evidence bead IDs / slice IDs
- judge model and prompt/rubric version
- verdict, confidence, and rationale

Revalidation with the same grounding hash should return the same verdict or explicitly create a new version with changed judge metadata.

**Files:** `core_memory/runtime/enrichment.py`, `core_memory/runtime/turn_flow.py`, association/claim validation paths

## 6. Monotonic sequencing for claim supersede chains

**Current behavior:** Claim/edge validation jobs can run asynchronously, and mutation semantics depend on emitted claim updates plus current-state resolution.

**Problem:** If a newer supersede validation finishes before an older one, completion order can corrupt "current truth" unless the chain has an independent ordering key.

**Fix:** Add a lightweight per subject+slot or per causal-chain sequence counter. Supersede/retract/conflict updates should apply according to chain sequence / observed-at ordering, not async job completion time.

**Files:** `core_memory/claim/update_policy.py`, `core_memory/persistence/store_claim_ops.py`, async enrichment/validation jobs

## 7. Automatic, durable, inspectable semantic indexing ergonomics

**Current behavior:** Core Memory already follows the right architecture for embeddings: bead write → semantic dirty/delta queue → embedding provider → vector backend upsert. But users can still experience it as separate operational steps if the queue/worker/rebuild state is not obvious or automatic enough.

**Problem:** Mongo-style managed vector indexing feels like “it just happens on write.” Core Memory intentionally keeps the embedding lifecycle portable and adapter-based, but the CLI/API should provide the same ergonomic confidence without hiding the architecture.

**Fix:** Make semantic indexing feel automatic while remaining explicit and inspectable:
- On bead write, reliably mark semantic state dirty and enqueue a durable delta.
- Provide a worker/side-effect path that drains semantic deltas without requiring users to remember manual rebuilds.
- Expose clear concepts and commands:
  - Semantic Delta Queue
  - Semantic Manifest
  - Embedding Provider
  - Vector Backend Adapter
  - Semantic Index Doctor
- CLI/API should support commands like:
  - `core-memory semantic status`
  - `core-memory semantic rebuild`
  - `core-memory semantic doctor`
  - `core-memory semantic tail`

**Impact:** Core Memory keeps its OSS-friendly portability across pgvector/qdrant/chromadb/provider choices, while giving users the managed-DB-style experience that semantic indexing “just happens on write.”

**Files:** `core_memory/retrieval/lifecycle.py`, `core_memory/retrieval/semantic_index.py`, `core_memory/runtime/jobs.py`, CLI semantic command modules

## 8. LLM-judged entity extraction and canonicalization in the live write path

**Status:** Closed in code on 2026-05-13. The bead-field judge already authors
`entities`; the entity registry now also runs a dedicated LLM-judged extraction /
canonicalization pass so aliases, canonical labels, and entity usefulness are decided by
the same semantic standard as the rest of the bead write path.

**Problem:** Generic NER is good at spotting names, but Core Memory needs more than NER:
it needs retrieval-useful entities, project/product/system concepts, canonical aliases,
and a refusal path for generic nouns or retrieval-only questions. Regex/NER can be a
fallback, not the policy authority.

**Fix:** Add an LLM-first entity judge that:
- extracts only durable named entities / project terms / systems / datasets / stable concepts
- reuses existing registry labels and aliases when a bead clearly refers to them
- records grounded aliases, entity kind, confidence, and evidence in provenance
- falls back narrowly to bead-provided entity labels when no LLM is configured
- keeps OpenClaw/plugin instructions tight: adapters may pass candidate entities, but the
  Core Memory write path owns final canonicalization

**Files:** `core_memory/entity/registry.py`, `core_memory/policy/bead_judge.py`,
`AGENT_INSTRUCTIONS.md`, `docs/integrations/openclaw/canonical_contract.md`,
`plugins/openclaw-core-memory-bridge/skills/core-memory/SKILL.md`, `tests/test_entity_registry.py`

## 9. Unify session-window enrichment crawler for associations, claims, entities, and promotion

**Status:** Planned. This is the architectural follow-up to #2, #3, #6, and #8.
Treat this as a contract-and-migration program, not a "new mega-crawler" rewrite:
Slice A must be behavior-preserving and establish a shared enrichment shape before any
semantic logic moves.

**Principle:** Enrichment is not a one-off cleanup step. It runs every turn over the
visible/session-window bead surface so memory can build on itself as natural conversation
progresses. Associations, claims, entity aliases, and promotion/lifecycle decisions should
therefore be facets of one session-window enrichment judgment, not independent semantic
passes competing to interpret the same text.

**Current observed shape:**
- `process_turn_finalized(...)` writes/persists the canonical turn bead first.
- Runtime builds a bounded crawler context with `build_crawler_context(..., limit=200)`
  over the session surface / visible bead IDs.
- `turn-enrichment` is enqueued by default via `CORE_MEMORY_ENRICHMENT_QUEUE=on`; if the
  queue is disabled, the same stages run inline as fallback.
- `run_turn_enrichment(...)` currently executes separate stages: association pass,
  heuristic claim extraction, preview associations, crawler merge, decision pass, claim
  updates, memory outcome, and quality metric.
- Claim extraction is currently mostly heuristic/turn-local (`claim/extraction.py`) and is
  attached after association application, while claim updates reconcile later against
  current state.
- Promotion decisions are split between crawler-authored promotion marks and the session
  decision pass.
- Entity canonicalization currently happens from bead `entities` during bead sync, with an
  LLM-first registry judge added in #8, but it is still not part of a single compound
  enrichment delta.

**Problem:** The code has the right cadence — every turn, session-window visible context —
but the semantic outputs are fragmented by subsystem. That can stack cost/latency, create
duplicate or conflicting judgments, and miss cross-signal opportunities where an
association clarifies a claim, a claim clarifies promotion, or an entity alias clarifies
both.

**Target shape:** Replace the separated semantic passes with one runtime-owned
session-window enrichment contract that can emit a compound delta:
- bead updates / lifecycle state changes
- associations and association lifecycle actions
- claim creations and claim updates
- entity upserts / alias links
- promotion decisions
- memory outcome metadata when applicable

The contract should reserve the live-system primitives needed by #5/#6-style grounding,
idempotency, and sequencing work from day one, without implementing the full validation /
eval layer in this slice:
- stable delta/job idempotency key
- evidence/context fingerprint
- model, rubric, and prompt version for LLM-judged outputs
- per-output dedupe keys
- ordering / sequence fields for claim and lifecycle updates
- provenance and confidence on every emitted item

The crawler should reason over:
- the new/current turn bead
- every bead in the visible session window
- existing associations among visible beads
- existing claims/current-state slots relevant to visible beads
- existing entity registry labels/aliases relevant to visible beads
- prior promotion/lifecycle states

**Non-goals:**
- Do not reprocess the whole archive every turn.
- Do not run separate LLM agents for entity extraction, claim extraction, association
  linking, and promotion when one session-window enrichment judgment can emit all deltas.
- Do not move semantic policy into OpenClaw/plugin bridge code.
- Do not implement the full grounding-hash validation or benchmark/eval layer in Slice A;
  do design `session_enrichment_delta.v1` so those primitives do not require a later
  contract break.

**Analysis plan before implementation:**
1. **Trace the current turn lifecycle end-to-end.** Produce a call graph from
   `process_turn_finalized(...)` through bead persistence, crawler context construction,
   enqueue/inline enrichment, association application, claim extraction/update, decision
   pass, and merge. Note exact inputs/outputs and idempotency keys.
2. **Inventory all existing delta schemas.** Enumerate accepted fields for
   `crawler_updates`, association lifecycle rows, claim rows, claim update rows, entity
   registry rows, promotion decisions, and memory outcome metadata. Mark which are append
   only, mutable, or derived projection fields.
3. **Map context surfaces.** Compare `read_session_surface(...)`,
   `build_crawler_context(...)`, `visible_bead_ids`, `window_bead_ids`, current-state claim
   reads, and entity registry reads. Identify what the unified crawler must see to make
   correct cross-signal decisions.
4. **Identify duplicated or conflicting judgments.** Find where bead-field judging,
   registry entity judging, heuristic claim extraction, decision pass, and association
   crawler each infer overlapping semantics. Decide which outputs remain in the initial
   bead judge and which move into the unified enrichment delta.
5. **Design the unified enrichment contract.** Define a versioned payload such as
   `session_enrichment_delta.v1` with bounded arrays, canonical relationship/claim types,
   explicit provenance, confidence, evidence/context refs, fingerprint fields,
   model/rubric/prompt version fields, per-output dedupe keys, lifecycle/claim sequencing
   keys, rationale/evidence fields, strict normalization, and quarantine for invalid rows.
6. **Define execution semantics.** Specify per-turn idempotency, partial failure behavior,
   ordering, dedupe keys, how later turns can update prior visible beads, explicit window
   bounds, and how async queue mode must remain equivalent to inline fallback mode.
7. **Plan migration slices.** Start by making current stages consume/produce a shared
   compound delta without changing behavior; then fold #3 association types; then fold
   entity upserts/aliases and retire #8's interim separate judge path where possible; then
   fold claims and claim updates with monotonic sequencing keys; then fold goal lifecycle;
   then return to #7 semantic indexing around the stabilized write/enrichment lifecycle.

**Preferred execution order:**
1. Land TODO/instruction pivot.
2. Complete #9 analysis pass.
3. Design `session_enrichment_delta.v1`.
4. Implement behavior-preserving adapter layer.
5. Fold #3 association types into the unified delta.
6. Fold entity upserts/aliases into the unified delta, replacing #8's interim separate
   judge path where possible.
7. Fold claims and claim updates, with monotonic sequencing keys included in the contract.
8. Fold goal lifecycle (#2).
9. Return to #7 semantic indexing around the stabilized write/enrichment lifecycle.

**Slice A acceptance criteria:**
- Inline and queued enrichment produce equivalent committed state.
- Re-running the same turn/enrichment job does not duplicate associations, claims,
  entities, or promotion marks.
- Existing behavior/tests remain preserved.
- New contract has strict normalization and quarantine paths for invalid rows.
- No OpenClaw/plugin bridge owns semantic policy.
- Window bounds are explicit and test-covered.
- Each emitted delta item carries provenance, evidence/context refs, confidence, and a
  stable dedupe key.

**Files:** `core_memory/runtime/turn_flow.py`, `core_memory/runtime/enrichment.py`,
`core_memory/association/crawler_contract.py`, `core_memory/claim/turn_integration.py`,
`core_memory/claim/update_policy.py`, `core_memory/entity/registry.py`,
`core_memory/runtime/decision_pass.py`, `core_memory/runtime/session_surface.py`,
`core_memory/persistence/store_claim_ops.py`, promotion persistence modules, tests covering
queued and inline enrichment parity.
