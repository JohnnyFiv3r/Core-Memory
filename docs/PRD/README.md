# PRD Index

Per-phase Product Requirements Documents for the Core Memory cleanup workstream.
See `docs/cleanup-plan.md` for phase descriptions, sequencing, and risk notes.
See `docs/status.md` for current completion state.

| File | Phase | Topic | Status |
|---|---|---|---|
| `00-ci-baseline.md` | 0 | CI + coverage baseline | Done |
| `01-dead-file-removal.md` | 1 | Delete confirmed dead files | Done |
| `02-circular-import-fix.md` | 2 | Fix mislabeled circular-import workarounds | Done |
| `03a-pydanticai-boundary.md` | 3A | Harden PydanticAI adapter boundary | Done |
| `04-graph-module-cleanup.md` | 4 | Remove `graph/api.py` compat facade | Done |
| `05-persistence-delegation-flatten.md` | 5 | Flatten persistence delegation chain | Done |
| `06-storage-adapter-boundary.md` | 6 | Unify StorageBackend + VectorBackend into capability tiers | Done |
| `07-neo4j-query-backend.md` | 7 | Graph backend abstraction (pluggable causal graph providers) | Done |
| `07b-execution-plan.md` | 7b | Neo4j read path execution plan | Done |
| `07b-qdrant-kuzu-migration.md` | 7b | Qdrant/Kuzu migration notes | Done |
| `08-init-wizard.md` | 8 | `core-memory init` wizard + `core-memory doctor` expansion | Done |
| `09-structural-consolidation.md` | 9 | Structural consolidation (runtime/, cli/, openclaw/) | Done |
| `10-documentation-consolidation.md` | 10 | Documentation consolidation | Done |
| `execution-plan-phases-0-7-10.md` | 0, 7e–7i, 10 | One-pass execution plan (Graphiti, Obsidian, docs) | Done |

## Capability PRDs

| File | Topic | Status |
|---|---|---|
| `external-data-bead-ingest.md` | External data bead ingest contract | Done |
| `multi-store-recall-fanout.md` | Multi-store recall fan-out (Ragie/PipeHouse) | Done |
| `eval-benchmark-layer.md` | Eval and benchmark layer (LoCoMo) | Done |
| `session-enrichment-delta-analysis.md` | Session enrichment delta — analysis | Done |
| `session-enrichment-delta-slice-b.md` | Session enrichment delta — slice B | Done |
| `execution-plan-search-quality-and-enrichment.md` | Search quality + enrichment plan | Done |
| `myelination-v2-continuity-strength.md` | Unified edge strength + continuity-depth manifest + geometry projections | **Superseded** → `myelination-reinforcement.md` (+ Dreamer V3 assembly depth / geometry) |
| `dreamer-v2-continuity-observer.md` | Dreamer as observer over worldline convergence / attractors / narratives | **Superseded** → `dreamer-continuity-engine.md` (storyline slice shipped + preserved) |

## Agency / Self-Model PRDs

These three are the active capability set for the agency layer (self-model,
scientific findings, reinforcement). They are mutually consistent and supersede
the older entries above.

| File | Topic | Status |
|---|---|---|
| `soul-files.md` | SOUL Files — agent-authored self-model + goal hierarchy (supersedes `reports/soul-synthesis-spec.md`) | **Draft v3** |
| `dreamer-continuity-engine.md` | Dreamer V3 — scientific continuity engine, assembly depth, storyline projection, future vectors; V4 target-states + agency backlog | **Draft v3** |
| `myelination-reinforcement.md` | Myelination V2 — audited association reinforcement & decay (edge-only) | **Draft v2** |

## Implementation status & deferred work

| Capability | Status |
|---|---|
| Myelination V2 (all reward sources + host guide) | ✅ Shipped (#202–#206) |
| Dreamer V3 Phase 1 — Assembly Depth, tension discovery | ✅ Shipped (#208, #209) |
| Dreamer V3 Phase 2 — goal decay, goal discovery | ✅ Shipped (#210, #211) |
| Dreamer V3 Phase 3 — future projection (narrative/attractor strength) | ✅ Shipped (#212) |
| SOUL Files — foundation, session-start injection, HTTP, Dreamer→SOUL bridge | ✅ Shipped (#213, #215, #216, #220) |
| Dreamer §15 — identity / value research (`value_candidate`, `identity_divergence_candidate` → IDENTITY.md via bridge) | ✅ Shipped (#221) |
| Dreamer §16.1 — geometry / continuity projection (read-only manifest + `GET /v1/dreamer/geometry`) | ✅ Shipped |

_All Dreamer V3 + SOUL agency-layer items are shipped; no deferred work remains in this set._
