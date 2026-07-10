# Core Memory — Status

**Last updated:** 2026-07-10

Single source of truth for open work across the cleanup workstream and
engine-correctness items. See `docs/cleanup-plan.md` for detailed phase
descriptions.

---

## Owned-ingestion companion contract

| Slice | Status |
|---|---|
| Versioned `chunk_turn_record.v1` HTTP ingest, idempotency, native hydration/adjacency, and version-filtered GC planning read | **Done in `memory.chunk_turns.v1`** |
| Chunk evidence-vector indexing plus resolve-up to parent section beads | **Pending** |
| Recall Contract v2 (`hydration`, legacy effort aliases, opaque `expand`) | **Pending** |

The chunk-turn boundary stores L2 source text in the engine's authoritative turn
archive. It does not make chunks causal retrieval units and does not imply that
the vector resolve-up or Recall v2 slices are complete.

---

## Cleanup workstream

**Architecture cleanup status:** complete at the architecture layer. The
checked-in architecture guard baseline has zero known debt, and the compatibility
surface ratchet is active for retained public compatibility paths. Remaining
public compatibility surfaces are tracked as a post-cleanup deprecation backlog
in `docs/compatibility_ledger.md`; future removal requires the ledger's
deprecation or breaking-change conditions.

The follow-up PRD truth sweep also completed: completed/locked historical PRDs
no longer carry stale open-checklist examples as current state. Use this file
and `docs/PRD/README.md` for current completion truth.

| Phase | Topic | Status |
|---|---|---|
| 0 | CI + Coverage Baseline | **Done** |
| 1 | Dead file removal | **Classified** — retained compatibility surfaces are ledgered; proven-dead files retired |
| 2 | Circular import fixes | **Done** |
| 3A | Harden PydanticAI boundary | **Done** |
| 4 | Classify `graph/api.py` compat facade | **Done at architecture layer** — retained public compatibility backlog |
| 5 | Persistence delegation flatten | **MRO flat; legacy mixin artifacts retired** |
| 6 | Storage adapter capability tiers | **Done** |
| 7a | `persistence/graph/` package + protocol + factory | **Done** |
| 7b | `Neo4jGraphBackend` read path + `KuzuGraphBackend` | **Done** |
| 7c | Write-side hooks | **Done** |
| 7d | `core-memory graph backend-sync` CLI | **Done** |
| 7e | `GraphitiGraphBackend` (self-hosted) | **Done** |
| 7f | Zep-hosted alias | **Done** |
| 7g | LLM client injection protocol | **Done** |
| 7h | `ObsidianSyncTarget` (BeadSyncTarget) | **Done** |
| 7i | Plugin API docs | **Done** |
| 8a | `core-memory setup init` wizard + layered config | **Done** |
| 8b | Mode-based wizard, doctor profiles, `config` subcommand, `demo` | **Done** |
| 9a–9h | Structural consolidation (runtime/, cli/, openclaw/) | **Done at architecture layer** — retained public compatibility backlog |
| 10a | Archive 11 stray `v2_p*` files | **Done** |
| 10b | Retire `docs/ARCHITECTURE.md` | **Done** |
| 10c | Update `architecture_overview.md` | **Done** |
| 10d | Audit and classify docs/ root | **Done** |
| 10e | Create `docs/status.md` | **Done** (this file) |
| 10f | Add `docs/PRD/README.md` | **Done** |
| 10g | Update `docs/index.md` | **Done** |

---

### Cleanup truth-audit note

The cleanup docs previously overstated some deletion/completion status. The current
tree still contains these retained compatibility surfaces, so treat them as
classified-retained files, not deleted files:

- `core_memory/persistence/encryption.py`
- `core_memory/graph/api.py`

`core_memory/persistence/encryption.py` is now classified in
`docs/compatibility_ledger.md` as a public optional compatibility module. It is
covered by `tests/test_persistence_encryption_compat.py` and should not be
deleted as dead-file cleanup.

`core_memory/graph/api.py` is now classified in `docs/compatibility_ledger.md`
as a public compatibility facade. Retain it until the ledger's deprecation and
removal conditions are satisfied.

The architecture guard baseline is now clean. The dedicated
`architecture-guards` CI workflow runs
`scripts/check_architecture_guards.py --baseline scripts/architecture_guards_baseline.json --fail-on-new`
for code, current docs, guard script, and guard baseline changes. If future work
needs an intentional exception, classify it in `docs/compatibility_ledger.md`.
The current checked-in baseline is zero; new architecture debt must not be
hidden by widening the baseline.

The compatibility usage guard is also active. It runs against
`scripts/compat_surface_usage_baseline.json` and currently allows only the
ledgered references for `core_memory.runtime.semantic_tasks`, `form_submission`,
`core_memory/retrieval/tools/memory_search.py`, `MemoryStore.dream(...)`, and
the legacy event-schema import path. New first-party reliance on those surfaces
is compatibility drift.

The semantic task provider runtime and verifier implementation now live in
`core_memory.policy`, and semantic task receipts live in
`core_memory.persistence`. The historical `core_memory.runtime.semantic_tasks`
runtime/verifier/receipt paths remain public compatibility facades. This shrank
the architecture guard baseline from 10 upward-import rows to 4.

`MemoryStore.dream(...)` remains a legacy convenience bridge, but it no longer
creates a static persistence-to-runtime import. Dreamer analysis is resolved at
call time through a local provider lookup, shrinking the architecture guard
baseline from 4 upward-import rows to 3 while preserving the method behavior.
The CLI Dreamer path now calls the runtime analysis surface directly, leaving
first-party store-bridge usage isolated to compatibility tests.

`core_memory.retrieval.lifecycle` remains the public semantic lifecycle and
autodrain surface, but its autodrain worker now resolves the runtime async-job
runner at call time. This preserves retrieval lifecycle behavior while shrinking
the architecture guard baseline from 3 upward-import rows to 2.

Bead post-write side effects now live in
`core_memory/runtime/post_write/bead_commit.py`. The persistence add-bead path
still performs the durable write, then calls the runtime post-write boundary for
Qdrant/graph/sync mirroring and association coverage enqueueing. This resolved
the final two architecture guard rows; the checked-in guard baseline is now 0.

The test-suite cleanup closeout is complete. The 2026-07-10 collection snapshot
selects 2,244 core-lane tests and deselects 42 optional backend/live tests; the
all-extras lane selects 2,281 tests, including the 37 optional backend tests,
while deselecting the 5 live Neo4j tests. Remaining `facade`,
`mixin_assembly`, and ledgered compatibility tests are maintained public-surface
coverage, not cleanup debt. See `docs/test_suite.md` for the dated audit
snapshot and environment-specific execution expectations.

The legacy persistence mixin artifacts
`core_memory/persistence/store_core_delegates_mixin.py` and
`core_memory/persistence/store_reporting_promotion_mixin.py` were retired after
their methods had already been inlined into `MemoryStore`; method-contract tests
continue to cover the flattened surface.

`core_memory/retrieval/pipeline/explain.py` was retired after import scans
proved no active caller used the old `build_explain` path. The live explain
payload remains inline in `core_memory.retrieval.pipeline`.

`core_memory/persistence/write_ops.py` was retired after import scans proved no
active caller used the old persistence shim. Write behavior remains on
`MemoryStore` and canonical runtime boundaries such as `process_turn_finalized`.

`core_memory/retrieval/trace.py` was retired after active callers migrated to
`core_memory.retrieval.pipeline.canonical.trace_request` or the public low-level
tool surface `core_memory.retrieval.tools.memory.trace`.

`core_memory/management.py` moved to `core_memory/management/__init__.py` to
clear root flat-file relocation debt without changing the public
`core_memory.management` import path or package-root management exports.

`core_memory/cli_handlers_semantic.py` was retired after import scans confirmed
the live semantic CLI surface already routes through
`core_memory/cli/handlers/semantic.py`.

`core_memory/runtime/goal_lifecycle.py` was retired after import scans confirmed
the live goal lifecycle pass already routes through
`core_memory/runtime/session/goal_lifecycle.py`.

`core_memory/runtime/session_enrichment_delta.py` was retired after import scans
confirmed the live session enrichment delta normalizer already routes through
`core_memory/runtime/session/session_enrichment_delta.py`.

`core_memory/runtime/source_envelope.py` moved to
`core_memory/runtime/ingest/source_envelope.py` after active callers were
migrated directly to the ingest-owned module. No root compatibility shim was
retained.

---

## Engine-correctness items (from `demo/TODO.md`)

| # | Item | Status |
|---|---|---|
| 1 | Replace echo-based `because` with LLM extraction | **Closed** |
| 2 | Goal lifecycle — resolution mechanism | **Closed** |
| 3 | Association relationship types | **Reopened** — the preview classifier currently fills missing relationships deterministically; `docs/PRD/agent-led-semantic-write-integrity.md` replaces that canonical mutation with agent-judged repair |
| 4 | Bead type classifier — questions misclassified as precedent | **Closed** |
| 5 | Grounding hashes for judged association/claim validation | **Done** — grounding-hash per-slot dedup in `_append_claim_update_rows` + WARNING telemetry |
| 6 | Monotonic sequencing for claim supersede chains | **Closed** — fully covered by `chain_seq` (verified) |
| 7 | Automatic, durable, inspectable semantic indexing ergonomics | **Done** — auto-drain thread, `semantic backfill`, extended status/doctor |
| 8 | LLM-judged entity extraction in live write path | **Closed** |
| 9 | Unify session-window enrichment crawler | **Done** — Slice A (analysis) and Slice B (enrichment_run_id idempotency gate, Stage 4 atomicity, delta envelope) complete |

---

## Capability items

| # | Item | Status |
|---|---|---|
| 10 | Multi-speaker attribution and identity persistence | **Done** |
| 10A | Multi-party transcript ingest (N-speaker gateway) | **Done** |
| 10B | Per-adapter `source_system` (Slack / Discord / Zoom-Otter MCP adapters) | **Done** — timestamp conversion bug fixed; real integration tests added |
| 11 | Myelination wiring | **Done** |
| 12 | Dreamer: latent theme synthesis | **Done** |
| 13 | Temporal recall API (`as_of`) | **Done** |
| 14 | Contradiction pressure and epistemic uncertainty | **Done** |
| 14A | `both_valid` resolution + `context_scope` claim discriminator | **Done** |
| 15 | Multi-store recall fan-out | **Done** — PipeHouse adapter retained with ThreadPoolExecutor fan-out, score normalization, unifying ID grouping; Ragie live fan-out retired before API sunset |
| 16 | External data bead ingest contract | **Done** |
| 17 | Eval and benchmark layer | **Done** — LoCoMo adapter in `benchmarks/locomo/` |
| 18 | Causal recall pipeline + retrieval quality (multi-source seeding, provenance/directional edge weights, because→edges, never-forget write path, causal benchmark) | **Done** — PR #191 |
| 19 | Ungated causal scoring (classified-intent + structural triggers; graph consulted at every effort tier) | **Done** — PR #192 |
| 20 | Edge lifecycle (usage reinforcement, decay floor, supersession penalty) | **Done** — PR #193, `docs/edge_lifecycle.md` |
| 21 | HTTP `/v1/memory/recall` parity with MCP/Python | **Done** — PR #194 |
| 22 | Worldline derivation (claim/entity/goal threads + membership projection) | **Done** — PR #195 |
| 23 | Myelination V2: audited association reinforcement and decay | **Done** — old continuity-strength PRD superseded by `docs/PRD/myelination-reinforcement.md`; shipped reward sources + host guide tracked in `docs/PRD/README.md` |
| 24 | Dreamer V3: scientific continuity engine, assembly depth, storylines, and future projection | **Done** — Dreamer v2 PRD superseded by `docs/PRD/dreamer-continuity-engine.md`; V4 target-states remain future work |
| 25 | Storylines: overlay layer (storyline_overlay.v1, convergence detector, decide-flow materialisation) + `derive_storylines` projection + HTTP route | **Done** |

---

## Open workstreams

### SOUL surfaces / host self-model
Core Memory owns governed SOUL read/write support: file projections, summary
metrics, proposal/review/apply flows, goal lifecycle endpoints, Dreamer bridge
hooks, and integrity check/repair surfaces. Identity synthesis as a durable
host self-model remains outside the causal graph (product-layer boundary
decision, 2026-06-10). Future target-state and agency primitives remain out of
scope until a new PRD promotes them.
**Status:** Engine support shipped; host self-model remains external.

### Demo TODO alignment
The paired adoption/API roadmap lives in `JohnnyFiv3r/Core-Memory-Demo` repo.
Engine-correctness items #5, #7, and #9 are **Done**. Item #3 is reopened under
the agent-led semantic write integrity PRD because its current deterministic
relationship fill violates the canonical authorship boundary.
Capability items #10–#14, #16–#17 are all closed. #15 is now **Done**.
See `docs/PRD/execution-plan-search-quality-and-enrichment.md` for the full plan.

### Agent-led semantic write integrity
The canonical contract requires agents to author semantic meaning while Core
Memory supplies typed validation, persistence, queueing, and lifecycle
guardrails. The current write path still permits deterministic fallback
authorship, drops known fields during creation normalization, and can report
semantic success before canonical bead existence is confirmed. The approved
plan preserves one canonical current-turn bead plus up to two explicitly
derived companion beads and uses full-schema delegated authorship for the
passive hosted OpenClaw capture path.

**Status:** Draft v1 / approved implementation sequence — documentation gate in
progress; code slices not yet shipped. See
`docs/PRD/agent-led-semantic-write-integrity.md`.

---

## References

- `docs/cleanup-plan.md` — detailed phase descriptions and per-step checkboxes
- `docs/compatibility_ledger.md` — compatibility surfaces and removal conditions
- `docs/PRD/` — per-phase PRD specs (`docs/PRD/README.md` for index)
- `demo/TODO.md` — engine-correctness items with full context
- `docs/architecture_overview.md` — canonical architecture reference
