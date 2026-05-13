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
