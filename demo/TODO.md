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

**Status: Resolved.** `core_memory/runtime/session/goal_lifecycle.py` now implements goal resolution detection, association linking from outcome to goal bead, and status transition logic. Tests live in `tests/test_goal_lifecycle.py`.

**Files:** `core_memory/runtime/session/goal_lifecycle.py`, `tests/test_goal_lifecycle.py`

## 3. Association relationship types

**Status:** Partially resolved. The `association_preview` path now assigns canonical relationship types (`caused_by`, `led_to`, `reinforces`, `transferable_lesson`, etc.) using `core_memory/association/preview.py`. The runtime explicitly treats `shared_tag` as a non-canonical heuristic match (see `engine.py` around `_queue_preview_associations`).

**Remaining gap:** The PydanticAI adapter path still defaults to `shared_tag` when no relationship type is specified by the agent. To fully close this, either have the agent provide explicit relationship types, or run the preview classifier on agent-proposed associations before committing them.

**Files:** `core_memory/association/preview.py`, `core_memory/runtime/engine.py` (`_queue_preview_associations`), `core_memory/integrations/pydantic_ai/`

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

## 8. Authentication for self-hosted MCP and HTTP server

**Current behavior:** The MCP server and HTTP integration server have no authentication outside of the hosted demo's access controls. Any process that can reach the server's port can read and write memory.

**Problem:** Self-hosted deployments (local agent harnesses, Docker, LAN-accessible instances) expose the full memory read/write surface without requiring a caller to prove identity. This is non-standard — even single-user self-hosted MCP servers conventionally use a shared secret or bearer token so that only the configured agent/harness can attach. Without it, any co-resident process or network peer can inject beads, corrupt claims, or exfiltrate the full memory store.

**Standard:** MCP servers — including self-hosted — typically gate connections with a static bearer token passed as an env var (`CORE_MEMORY_TOKEN` or similar) and checked on every request. The HTTP server already has dead-code stubs for `x-memory-token` header checking; these need to be wired to an actual enforced secret.

**Fix:**
- Add a `CORE_MEMORY_TOKEN` env var (or `--token` CLI flag) to the HTTP and MCP server startup paths.
- Enforce it on all write endpoints; optionally enforce on reads too (configurable).
- Return `401 Unauthorized` with a clear message when the token is missing or wrong.
- Document the env var in `AGENT_INSTRUCTIONS.md` and the self-hosting setup guide.
- If token is unset, log a prominent warning at startup (do not silently allow open access).

**Files:** `core_memory/integrations/http/server.py`, `core_memory/integrations/mcp/server.py`, `AGENT_INSTRUCTIONS.md`, deployment/setup docs

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

## 8b. Turn/flush ordering: ok=True before bead is persisted (known architectural risk)

**Status:** Pre-existing. Not introduced by this branch.

**Risk:** When `CORE_MEMORY_ENRICHMENT_QUEUE=on` (the default), `process_turn_finalized()`
returns `ok=True` before the bead is durably persisted to `index.json`. The bead write
is enqueued as a side-effect that runs asynchronously. A caller that trusts the `ok=True`
response and immediately queries `index.json` or the vector index may observe the bead
missing.

**Affected code:**
- `core_memory/runtime/passes/enrichment.py` — `CORE_MEMORY_ENRICHMENT_QUEUE` defaults to `"on"`
- `core_memory/runtime/queue/side_effect_queue.py` — queue drain is the persistence path when queue is enabled
- `tests/test_f_w1_enrichment_queue.py` — currently only asserts session JSONL exists, not `index.json`

**Recommended strengthening:** Add an assertion in `test_f_w1_enrichment_queue.py` that the
bead appears in `index.json` (not just the session JSONL) after the queue is drained. This
would catch any future regression where the queue drain path stops persisting to the index.

**Fix scope:** This requires clarifying whether `ok=True` means "turn accepted into queue"
or "turn durably committed". Currently it means the former. If callers need the latter
semantics, `process_turn_finalized` should either flush the queue before returning, or
return a separate `committed: bool` field indicating durable persistence status. Full fix
tracked under #9 (unified session-window enrichment) which will revisit enrichment
queue semantics.

---

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

**Analysis artifact before implementation:**
Do not implement delta code until a committed artifact names the current write paths and
idempotency boundaries precisely. That artifact should include:
1. **Current call graph.** Trace from `process_turn_finalized(...)` through bead
   persistence, crawler context construction, queued/inline enrichment, association
   application, claim extraction/update, decision pass, merge, and side-effect commits.
2. **Current schemas and persistence targets.** Enumerate accepted fields and write targets
   for `crawler_updates`, association lifecycle rows, claim rows, claim update rows, entity
   registry rows, promotion decisions, and memory outcome metadata. Mark which are append
   only, mutable, or derived projection fields.
3. **Current idempotency/dedupe keys.** Name the exact boundaries that prevent duplicate
   associations, claims, entities, promotion marks, lifecycle updates, queue jobs, and
   side-effect commits today; call out where no boundary exists.
4. **Current window/context surfaces.** Compare `read_session_surface(...)`,
   `build_crawler_context(...)`, `visible_bead_ids`, `window_bead_ids`, current-state claim
   reads, and entity registry reads. Identify what the unified crawler must see to make
   correct cross-signal decisions.
5. **Overlapping semantic judgments.** Find where bead-field judging, registry entity
   judging, heuristic claim extraction, decision pass, and association crawler each infer
   overlapping semantics. Decide which outputs remain in the initial bead judge and which
   move into the unified enrichment delta.
6. **Proposed field inventory for `session_enrichment_delta.v1`.** Include bounded arrays,
   canonical relationship/claim types, explicit provenance, confidence, evidence/context
   refs, fingerprint fields, model/rubric/prompt version fields, per-output dedupe keys,
   lifecycle/claim sequencing keys, rationale/evidence fields, strict normalization, and
   quarantine for invalid rows. This is a design inventory, not implementation.
7. **Migration risks and Slice A parity tests.** Define expected equivalence, known fields
   that may legitimately differ, and test fixtures needed before the adapter layer lands.

**Preferred execution order:**
1. Land TODO/instruction pivot.
2. Complete the committed #9 analysis artifact.
3. Design `session_enrichment_delta.v1`.
4. Implement behavior-preserving adapter layer.
5. Fold #3 association types into the unified delta with a strict relationship enum,
   normalization warnings/provenance, and quarantine for unknown labels; if relationship
   typing depends materially on entity resolution quality, keep this slice minimal or swap
   it after entity folding.
6. Fold entity upserts/aliases into the unified delta, replacing #8's interim separate
   judge path where possible.
7. Fold claims and claim updates, with monotonic sequencing keys, evidence refs,
   grounding/context fingerprints, and judge/rubric versions included in the contract.
8. Fold goal lifecycle (#2), after claims/current-state evidence is available.
9. Return to #7 semantic indexing around the stabilized write/enrichment lifecycle.

**Slice A acceptance criteria:**
- Inline and queued enrichment produce equivalent committed state. Equivalent means matching
  canonical row content, dedupe keys, lifecycle state, visible IDs, and normalized
  projections; raw JSON byte equality is not required for timestamps, UUIDs, queue metadata,
  model diagnostics, or other expected nondeterministic fields.
- Re-running the same turn/enrichment job does not duplicate associations, claims,
  entities, or promotion marks.
- Existing behavior/tests remain preserved.
- New contract has strict normalization and quarantine paths for invalid rows.
- No OpenClaw/plugin bridge owns semantic policy.
- Window bounds are explicit and test-covered. Captured bounds must include session id,
  current turn id/bead id, visible bead ids, window source, max window size,
  excluded/overflow counts when applicable, and context/evidence fingerprint.
- Each emitted delta item carries provenance, evidence/context refs, confidence, and a
  stable dedupe key.

**Files:** `core_memory/runtime/turn_flow.py`, `core_memory/runtime/enrichment.py`,
`core_memory/association/crawler_contract.py`, `core_memory/claim/turn_integration.py`,
`core_memory/claim/update_policy.py`, `core_memory/entity/registry.py`,
`core_memory/runtime/decision_pass.py`, `core_memory/runtime/session_surface.py`,
`core_memory/persistence/store_claim_ops.py`, promotion persistence modules, tests covering
queued and inline enrichment parity.

---

## Capability Roadmap

Items below are forward-looking capability additions, not correctness fixes. They build on the durable write/enrichment foundation established in #1–9. Priority order is given at the end of each item.

## 10. Multi-speaker attribution and identity persistence

**Current state:** The transcript ingest path accepts a `speaker` field and records it on turn envelopes, but speaker labels are treated as opaque strings. There is no identity resolution, no alias merging, no cross-session persistence of who a speaker is, and no mechanism to attach claims or associations to a resolved speaker entity rather than just a session.

**Problem:** Without speaker identity, Core Memory is fundamentally single-thread memory. The moment a transcript has more than one participant — Slack threads, Discord, meetings, email chains, group agents — causal chains cannot be attributed. "We decided to drop Kubernetes" is unresolvable when you cannot tell who "we" is. This blocks the transition from conversational memory to distributed cognition memory.

**Required research phase first:** Study transcript export formats from target systems (Slack, Discord, email threads, Zoom/meeting transcripts, GitHub discussions) before locking schema. Speaker label representations vary widely: usernames, email addresses, display names, thread-local aliases, role labels ("assistant", "bot", "system"), and changing handles across sessions. Schema must embrace this uncertainty rather than assume clean identity.

**Target schema shape:**
```json
{
  "speaker_observed": "johnnyfiv3r",
  "resolved_entity_id": "user_142",
  "resolution_confidence": 0.91,
  "source_system": "discord",
  "aliases": ["Johnny", "@john", "johnnyfiv3r"],
  "role": "founder"
}
```

**Approach:**
- Add a speaker identity resolver that maps `speaker_observed` → `resolved_entity_id` using the existing entity registry (alias resolution, confidence, provenance already exist)
- The resolution is probabilistic: `resolution_confidence` reflects alias match quality, not a binary lookup
- Claims should be attachable to a `resolved_entity_id` so "user_142 believes X" persists across sessions even when user_142 appears under different aliases
- Causal chains can then traverse entities, not just sessions — organization-level reasoning becomes possible
- Transcript ingest surfaces the multi-speaker turn schema (`feat: add multi-speaker turn schema`, already merged) but identity resolution is not yet wired

**Guardrails:**
- The schema must maintain four distinct layers: observed label / resolved identity / organizational role / entity graph node. Do not collapse them.
- Resolution logic lives in the entity registry, not in the transcript ingest surface. Ingest passes observed labels; resolution is a separate concern.
- Uncertainty is permanent: `resolution_confidence` is a first-class field, not a hidden implementation detail. Low-confidence resolutions must be inspectable and correctable.
- This feature must not require any one source system to be authoritative. Attribution works with whatever labels the source provides.

**Priority:** Immediate — after benchmarks. Foundational for all multi-participant memory.

**Files:** `core_memory/entity/registry.py`, `core_memory/transcript_ingest.py`, `core_memory/schema/models.py` (speaker attribution fields), `core_memory/persistence/store_add_bead_ops.py`, new `core_memory/entity/speaker_resolver.py`

---

## 11. Myelination: active decay, retrieval-routing reinforcement, and adaptive association strength

**Current state:** `core_memory/runtime/myelination.py` exists and computes candidate myelination scores. The concept is present but the scoring is not yet used to guide retrieval routing, and the decay/strengthening signal is not derived from retrieval outcomes or contradiction pressure.

**Problem:** Static association weights are graph scoring, not cognitive dynamics. A memory system where edges never change their effective importance based on use cannot learn. The goal is for Core Memory's association graph to become a learned retrieval topology — edges that have repeatedly served as successful explanatory conduits become reinforced; edges that have been contradicted, bypassed, or retrieved without payoff decay.

**Critical distinction:** Strengthening is not `association.weight += 1`. The signal is **successful explanatory utility** — did traversing this edge actually help answer a query? An important memory that is rarely retrieved should not decay just because it is infrequently accessed. The measure is: when this edge is traversed, does the retrieval succeed? If yes, reinforce. If the path is bypassed or the evidence is low-confidence, decay.

**Approach:**
- **Retrieval outcome feedback:** When `recall()` returns evidence, record which bead IDs and association edges were in the returned evidence path. On a positive retrieval outcome (status: answered, evidence scored high), increment a `retrieval_utility_score` on those edges. On a low-confidence or empty result, apply a mild decay signal to traversed edges.
- **Contradiction pressure decay:** When an association's source or target bead has an active contradiction (claim conflict, supersede with high chain_seq), apply a decay signal to that edge's effective weight.
- **Multi-hop reinforcement:** Track retrieval paths that span multiple hops. Stable multi-hop paths that consistently resolve queries become "cognitive highways" — reinforce the full path, not just individual edges.
- **Operate on all memory primitives:** Myelination signals apply to associations, entity aliases, claim slots, goal resolution edges, and retrieval paths — not just bead-to-bead edges.
- **Guide retrieval:** Eventually, retrieval planning (`core_memory/retrieval/retrieval_planner.py`) uses myelination scores to prefer strongly reinforced paths and de-prioritize decayed edges. This is the "path of least resistance" routing — the system begins predicting which traversal is likely to succeed before executing it.

**Guardrails:**
- Myelination scores are metadata on existing graph structures — they must not mutate bead content or claim state.
- Decay must never cause a fact to be suppressed or hidden. A decayed association is still queryable; it is just de-prioritized in ranking.
- The scoring model must be inspectable: every score change must carry a reason code (retrieval_success, retrieval_failure, contradiction_pressure, multi_hop_reinforcement) and a timestamp.
- Myelination must not be a write path — it runs as an async enrichment pass, not inline during turn finalization.
- Retrieval routing uses myelination as a signal, not a gate. The highest-myelination path is preferred but not exclusively followed.

**Priority:** Immediate — after benchmarks. Most differentiated capability in the system.

**Files:** `core_memory/runtime/myelination.py`, `core_memory/retrieval/retrieval_planner.py`, `core_memory/retrieval/evidence_scoring.py`, `core_memory/runtime/retrieval_feedback.py` (already exists — wire it), `core_memory/runtime/jobs.py` (myelination as async job), association persistence fields

---

## 12. Dreamer: latent theme synthesis and abstraction formation

**Current state:** `core_memory/runtime/dreamer.py`, `dreamer_candidates.py`, and `dreamer_eval.py` exist. Dreamer runs as a cron-style job and produces candidate correlations. The candidates are inspectable but the synthesis layer — identifying recurring motifs, latent themes, and higher-order abstractions across the memory graph — is underdeveloped.

**Problem:** Without a synthesis layer, Core Memory accumulates facts but does not form abstractions. A system that has seen "Redis removed", "Kafka adopted", "Kubernetes dropped", and "PostgreSQL chosen" over twelve months should be able to surface the latent theme "shift toward operational simplicity" — not as a fact, but as a candidate abstraction. That is a categorically different capability from retrieval.

**Critical constraint:** Dreamer must never invent or assert truth. Every output is a provisional memory object — a hypothesis raised to the user, not a fact inserted into the graph. The status is always `unreviewed` until explicitly accepted. This is non-negotiable.

**Approach:**
- **Focus on connecting themes:** Dreamer's primary job is pattern recognition across causal history — finding recurring motifs, unresolved tensions, and candidate abstractions that span multiple sessions. It does not generate new facts; it synthesizes observed ones.
- **Provisional memory objects as first-class output:** Every Dreamer output becomes a structured candidate bead:
  ```json
  {
    "type": "proposed_theme",
    "confidence": 0.41,
    "generated_by": "dreamer",
    "related_bead_ids": ["...", "..."],
    "status": "unreviewed",
    "because": "Three decisions across Q1–Q3 each reduced operational surface area"
  }
  ```
  This makes the synthesis process inspectable, causally grounded, and correctable. Abstractions themselves gain provenance.
- **Detection targets:** recurring causal motifs, unresolved contradiction clusters, candidate high-order abstractions, latent themes across entities/goals/decisions, potential causal highways that myelination has not yet reinforced
- **User-facing surface:** Dreamer raises suggestions to the user — the agent surfaces them as candidates for review. The user approves/rejects. Approved candidates enter the graph with `status: accepted` and become available for recall. Rejected candidates are archived with the rejection reason for future learning.
- **Append-only safety:** Because Core Memory is append-only with timestamps, Dreamer can reason over evolution, repetition, convergence, and drift across the full history — most memory systems cannot.

**Guardrails:**
- Dreamer outputs are always `proposed_theme` or equivalent provisional type — never `decision`, `lesson`, `precedent`, or any promotable type without explicit user acceptance.
- Dreamer must not write to the bead store directly. It emits candidates to `dreamer-candidates.json`; the user or an explicit accept/reject flow promotes them.
- Dreamer is not a hallucination engine. Every candidate must cite specific `related_bead_ids` from the actual graph. A candidate with no grounded bead evidence must be quarantined.
- The synthesis runs on the memory graph, not on raw conversation text. Dreamer reasons over committed beads, associations, claims, and goals — not over turn transcripts.

**Priority:** Next layer — after attribution and myelination. Stretch goal for the "learns over time" narrative.

**Files:** `core_memory/runtime/dreamer.py`, `core_memory/runtime/dreamer_candidates.py`, `core_memory/runtime/dreamer_eval.py`, `core_memory/runtime/jobs.py` (dreamer cron), new user-facing accept/reject surface

---

## 13. Temporal state resolution as first-class recall API surface

**Current state:** `resolve_all_current_state(root, as_of="2026-03-01")` already exists in `core_memory/claim/resolver.py:17` and is correctly implemented — it uses `claim_visible_as_of`, `claim_temporal_sort_key`, and `is_claim_current` from `core_memory/temporal.py`. The underlying data model is complete. The capability is not exposed at the `recall()` API surface.

**Problem:** "What database were we using in March?" is unanswerable through the public `recall()` interface because `as_of` is not a parameter it accepts or passes through. Temporal state querying is the correct answer to audit, compliance, debugging, and project history queries. The data model already supports it; the API surface does not.

**Approach:**
- Add `as_of: str | None = None` parameter to `recall()` in `core_memory/retrieval/agent.py`
- Pass `as_of` through retrieval planning so semantic search, lexical search, and claim slot resolution all respect the temporal boundary
- In `RecallResult`, surface `as_of` in the response metadata and annotate evidence items with their `created_at` relative to the `as_of` boundary
- Add `as_of` parameter to `POST /api/recall` in the demo
- Surface `as_of` in the `core-memory recall` CLI command

**Guardrails:**
- `as_of` is a read constraint, not a write constraint. It filters what is visible; it never mutates state.
- If `as_of` is in the future or otherwise invalid, return an error — do not silently ignore it.
- The `as_of` boundary must be applied consistently across all retrieval tiers (semantic, lexical, claim, causal). Inconsistent application — where semantic search ignores the boundary but claim resolution respects it — is worse than no temporal support at all.
- This feature exposes existing primitives. It must not add a new temporal data model; it uses `core_memory/temporal.py` as the sole temporal authority.

**Priority:** Immediate after benchmarks — the data model is already there. This is mostly API surface work.

**Files:** `core_memory/retrieval/agent.py`, `core_memory/retrieval/contracts.py`, `core_memory/retrieval/retrieval_planner.py`, `core_memory/claim/resolver.py` (already implemented), `core_memory/temporal.py` (already implemented), demo `app.py`, CLI recall command

---

## 14. Contradiction pressure and epistemic uncertainty on associations

**Current state:** Claims can be in `conflict` status via `store_claim_ops.py`. Associations are append-only with timestamps. There is no mechanism to propagate claim conflict into an association-level uncertainty signal, and there is no epistemic pressure score that increases when contradictory evidence accumulates.

**Problem:** Most memory systems collapse ambiguity too early — they pick a winner and discard the conflict. Organizations and long-running projects do not work this way: two people genuinely disagree, or a claim is made and then immediately contradicted by new evidence, and both facts are real. A memory system that silently resolves ambiguity is less accurate than one that surfaces it.

**Approach:**
- **Epistemic conflict score on claim pairs:** When two claims share the same `subject:slot` but have conflicting values and are both within a recent time window, compute an `epistemic_conflict_score` (0.0–1.0) that reflects how unresolved the conflict is. High chain_seq gap with no supersede → high pressure. Low gap with recent supersede → low pressure.
- **Uncertainty propagation to associations:** Associations whose source or target bead carries high epistemic conflict score inherit a `uncertainty_pressure` field. An association built on contested evidence is itself uncertain.
- **Append-only is the enabler:** Because Core Memory never overwrites, contradiction itself is evidence. The accumulation of contradicting claims over time is a measurable signal. The timestamps on conflicting claims tell you how long the conflict has persisted — a conflict that has lasted six months is categorically different from one that was resolved in the same session.
- **Human review routing:** When `epistemic_conflict_score` exceeds a configurable threshold, surface the conflict as a candidate for user review — similar to Dreamer candidates but driven by contradiction rather than pattern detection.
- **Query-time surfacing:** `recall()` should surface active epistemic conflicts relevant to the query in a `conflicts` field on `RecallResult`, so the user knows when the answer is contested rather than settled.

**Guardrails:**
- Epistemic conflict score is a read-derived signal — it must never cause a claim to be deleted, suppressed, or hidden. Conflicting claims remain fully queryable.
- The score is computed from the claim graph, not from text similarity. Do not use semantic similarity as a proxy for contradiction.
- Uncertainty propagation to associations is additive metadata, not a mutation of the association's content or relationship type.
- The conflict surfacing in `recall()` is informational — the recall result still includes the best available evidence. The `conflicts` field tells the user "this answer is contested"; it does not refuse to answer.
- Human review routing is a suggestion surface identical to Dreamer candidates. The system raises the conflict; the user resolves it. The system never auto-resolves epistemic conflicts.

**Priority:** Next layer — after attribution and myelination. High strategic value; moderate implementation complexity.

**Files:** `core_memory/persistence/store_claim_ops.py` (conflict score computation), `core_memory/claim/resolver.py`, `core_memory/claim/resolver_helpers.py`, `core_memory/retrieval/contracts.py` (RecallResult.conflicts field), association persistence fields, new `core_memory/claim/epistemic.py`

---

## Priority order

**Immediate — after benchmarks:**
1. **#13 Temporal state API** — data model already complete; mostly surface work; immediate value for audit/history queries
2. **#10 Multi-speaker attribution** — research phase first; foundational unlock for all multi-participant memory
3. **#11 Myelination** — most differentiated capability; primitives in place

**Next layer:**
4. **#14 Contradiction pressure** — high value; moderate complexity; builds on existing claim conflict machinery
5. **#12 Dreamer synthesis** — primitives in place; focused upgrade toward abstraction formation; requires myelination signals as input for meaningful theme detection
