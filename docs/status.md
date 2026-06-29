# Core Memory — Status

**Last updated:** 2026-06-29

Single source of truth for open work across the cleanup workstream and
engine-correctness items. See `docs/cleanup-plan.md` for detailed phase
descriptions.

---

## Cleanup workstream

| Phase | Topic | Status |
|---|---|---|
| 0 | CI + Coverage Baseline | **Done** |
| 1 | Dead file removal | **Active compatibility debt** — retained candidates pending classification |
| 2 | Circular import fixes | **Done** |
| 3A | Harden PydanticAI boundary | **Done** |
| 4 | `graph/api.py` compat facade removal | **Active public compatibility debt** — classified in ledger; retain until deprecation/removal condition |
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
| 9a–9h | Structural consolidation (runtime/, cli/, openclaw/) | **Mostly done; retained relocation debt pending classification** |
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
tree still contains these retained candidates, so treat them as active
classification debt, not deleted files:

- `core_memory/persistence/encryption.py`
- `core_memory/persistence/write_ops.py`
- `core_memory/graph/api.py`
- `core_memory/cli_handlers_semantic.py`

`core_memory/graph/api.py` is now classified in `docs/compatibility_ledger.md`
as a public compatibility facade. Retain it until the ledger's deprecation and
removal conditions are satisfied.

Current architecture guard debt is snapshotted in
`scripts/architecture_guards_baseline.json`. When cleanup resolves a row, shrink
the baseline in the same PR.

The legacy persistence mixin artifacts
`core_memory/persistence/store_core_delegates_mixin.py` and
`core_memory/persistence/store_reporting_promotion_mixin.py` were retired after
their methods had already been inlined into `MemoryStore`; method-contract tests
continue to cover the flattened surface.

`core_memory/retrieval/pipeline/explain.py` was retired after import scans
proved no active caller used the old `build_explain` path. The live explain
payload remains inline in `core_memory.retrieval.pipeline`.

---

## Engine-correctness items (from `demo/TODO.md`)

| # | Item | Status |
|---|---|---|
| 1 | Replace echo-based `because` with LLM extraction | **Closed** |
| 2 | Goal lifecycle — resolution mechanism | **Closed** |
| 3 | Association relationship types | **Done** — preview classifier fills missing relationship in `apply_crawler_updates` |
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
| 15 | Multi-store recall fan-out | **Done** — Ragie + PipeHouse adapters, ThreadPoolExecutor fan-out, score normalization, unifying ID grouping |
| 16 | External data bead ingest contract | **Done** |
| 17 | Eval and benchmark layer | **Done** — LoCoMo adapter in `benchmarks/locomo/` |
| 18 | Causal recall pipeline + retrieval quality (multi-source seeding, provenance/directional edge weights, because→edges, never-forget write path, causal benchmark) | **Done** — PR #191 |
| 19 | Ungated causal scoring (classified-intent + structural triggers; graph consulted at every effort tier) | **Done** — PR #192 |
| 20 | Edge lifecycle (usage reinforcement, decay floor, supersession penalty) | **Done** — PR #193, `docs/edge_lifecycle.md` |
| 21 | HTTP `/v1/memory/recall` parity with MCP/Python | **Done** — PR #194 |
| 22 | Worldline derivation (claim/entity/goal threads + membership projection) | **Done** — PR #195 |
| 23 | Myelination v2: unified continuity strength + geometry projections | **Proposed** — `docs/PRD/myelination-v2-continuity-strength.md` |
| 24 | Dreamer v2: continuity observer (convergence/attractor/narrative observations) | **Partially shipped** — storyline overlay slice done; see PRD update |
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
Engine-correctness items #3, #5, #7, #9 are all **Done**.
Capability items #10–#14, #16–#17 are all closed. #15 is now **Done**.
See `docs/PRD/execution-plan-search-quality-and-enrichment.md` for the full plan.

---

## References

- `docs/cleanup-plan.md` — detailed phase descriptions and per-step checkboxes
- `docs/compatibility_ledger.md` — compatibility surfaces and removal conditions
- `docs/PRD/` — per-phase PRD specs (`docs/PRD/README.md` for index)
- `demo/TODO.md` — engine-correctness items with full context
- `docs/architecture_overview.md` — canonical architecture reference
