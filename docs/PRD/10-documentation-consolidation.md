# PRD: Documentation Consolidation

**Phase:** 10
**Status:** Complete — docs index/status/PRD navigation and archive cleanup shipped
**Prerequisite:** Phase 9 complete (structural changes must be reflected in architecture docs)

---

## Current implementation note

Phase 10 is complete in the current tree. `docs/status.md` lists 10a–10g as
done, `docs/index.md` is the canonical navigation entrypoint,
`docs/PRD/README.md` indexes cleanup and capability PRDs, and
`docs/architecture_overview.md` is the live architecture reference. The former
`docs/ARCHITECTURE.md` now lives under `docs/archive/history/`, and the stray
root-level `v2_p*` phase artifacts have been archived.

The root docs classification work is represented by `docs/index.md`,
`docs/status.md`, `docs/compatibility_ledger.md`, and the reports/archive
directories. Some historically named root docs remain because they were
classified as current or reference material; their presence is not open Phase 10
debt by itself.

The plan below is retained as historical rationale for the documentation
consolidation. Statements about missing navigation, split TODO state, or
root-level phase artifacts refer to the pre-implementation baseline.

---

## Historical problem

The docs directory has accreted through 20+ numbered development phases and now contains
four distinct problems:

1. **Phase artifacts at the wrong level.** `v2_p9_kickoff.md`, `v2_p17_*.md` through
   `v2_p22_*.md` sit at `docs/` root. Most `v2_p*` files are already in
   `docs/archive/history/` — these 11 are stragglers.

2. **Two architecture documents that contradict each other.** `docs/ARCHITECTURE.md` (74
   lines) references file names from before the v2 rename
   (`event_ingress.py`, `event_state.py`, `memory_engine.py`) — none of those exist
   anymore. `docs/architecture_overview.md` (58 lines) is the live one but both are
   listed side-by-side in `docs/index.md` without indicating which is current.

3. **The docs root has ~30 reference files with no signal about which are current, stale,
   or superseded.** `REFACTOR_NOTES.md`, `adapter_layer_inventory.md`,
   `schema_inventory_baseline.md`, `retrieval-canonical-v9-execution.md`, `springai_adapter.md`
   are example files that may have been superseded by later work. A new contributor has
   no way to know without reading all of them.

4. **TODO tracking is split across two files** in different locations with different
   formats: `demo/TODO.md` (engine correctness items #1–#7) and
   `docs/reports/todo-validation-2026-05-15.md` (status of those items). The cleanup
   workstream now adds a third document (`docs/cleanup-plan.md`). Finding "what is
   currently open" requires reading all three.

---

## Success criteria / outcome

1. All `v2_p*` phase artifacts at `docs/` root have been moved to `docs/archive/history/`.
2. `docs/ARCHITECTURE.md` is archived. `docs/architecture_overview.md` is updated to
   reflect the post-Phase-9 directory structure and is the single architectural reference.
3. Every file at `docs/` root has a clear signal in `docs/index.md` about its status:
   current / reference / archived. No unlisted files.
4. `docs/index.md` is the verified entry point for docs navigation — it links
   `docs/cleanup-plan.md` and `docs/PRD/` and its architecture section points only to
   live documents.
5. A new `docs/PRD/README.md` indexes all PRD files with one-line descriptions.
6. The `docs/reports/todo-validation-2026-05-15.md` is merged into or superseded by a
   single `docs/status.md` that tracks: open correctness items (from `demo/TODO.md`),
   open cleanup items (from `docs/cleanup-plan.md`), and closed items.
7. All links in `docs/index.md` resolve (no 404 links to files that were moved in
   Phase 9).

---

## Historical sub-task 10a — Archive phase artifacts from docs root

Move these 11 files to `docs/archive/history/`:

- `v2_p9_kickoff.md` *(stray copy; main is already in archive/history/)*
- `v2_p17_consolidate_gate.md`
- `v2_p17_kickoff.md`
- `v2_p18_closeout_checklist.md`
- `v2_p18_kickoff.md`
- `v2_p19_closeout_checklist.md`
- `v2_p19_kickoff.md`
- `v2_p20_closeout_checklist.md`
- `v2_p20_kickoff.md`
- `v2_p21_kickoff.md`
- `v2_p22_notes.md`

Check for broken `docs/index.md` links after moving (none of these are currently listed
in `docs/index.md`, but confirm).

---

## Historical sub-task 10b — Retire `docs/ARCHITECTURE.md`

`docs/ARCHITECTURE.md` references pre-v2 file names:
- `event_ingress.py` (now `runtime/turn/ingress.py` after Phase 9)
- `event_state.py` (now `runtime/state.py`)
- `event_worker.py` (now `runtime/queue/worker.py` after Phase 9)
- `memory_engine.py` (now `runtime/engine.py`)

It also defines "Five Canonical Centers" that may have shifted with v2 architecture.

**Process:**
1. Read both `ARCHITECTURE.md` and `architecture_overview.md`.
2. Extract any content from `ARCHITECTURE.md` that is not covered by
   `architecture_overview.md` and is still accurate.
3. Merge that content into `architecture_overview.md`.
4. Move `ARCHITECTURE.md` to `docs/archive/history/`.
5. Update `docs/index.md` to remove the `ARCHITECTURE.md` link and add a note:
   "Archived — see `architecture_overview.md`."

---

## Historical sub-task 10c — Update `architecture_overview.md` for post-Phase-9 layout

After Phases 4–9 ship, `architecture_overview.md` needs to reflect:

- `runtime/` now has subdirectories (turn, flush, session, passes, queue, dreamer,
  observability)
- `graph/api.py` is a retained public compatibility facade; split modules and
  package-level re-exports are the forward surface
- `cli/` is a package with parsers/ and handlers/
- `integrations/openclaw/` is now a proper subdirectory
- `StorageBackend` protocol has capability tiers (Phase 6)
- `core-memory init` and `core-memory doctor` are expanded (Phase 8)

The architecture doc should describe the **intended structure**, not just what exists.
It should be short enough to read in 5 minutes. Reference `docs/index.md` for deeper
dives. Aim for 100–150 lines.

At minimum, the doc should answer:
1. What are the layers and what does each own?
2. Where does a write enter, what touches it, where does it land?
3. Where does a read/recall enter, what tiers does it pass through?
4. How are framework integrations structured (adapter pattern)?
5. What is pluggable (storage, vector, graph)?

---

## Historical sub-task 10d — Audit and classify docs root files

For each file at `docs/` root (excluding `index.md` itself and PRD/, archive/, reports/
subdirectories), determine its status:

| Status | Meaning | Action |
|--------|---------|--------|
| **Current** | Actively maintained; content is accurate | Keep; list in index.md with "reference" label |
| **Stale-but-useful** | Historically informative; no longer fully accurate | Add "last verified" date to header; keep with warning |
| **Superseded** | Content moved to a better location | Add redirect note; move to archive/ |
| **Snapshot** | Point-in-time audit or report | Move to `docs/reports/` or `docs/archive/` |

Files to classify (conduct per-file read + decision):

| File | Likely status | Notes |
|------|--------------|-------|
| `REFACTOR_NOTES.md` | Superseded | v2 refactor is complete; content likely in archive |
| `adapter_layer_inventory.md` | Snapshot | Superseded by `adapter_parity_matrix.md`? Verify. |
| `adapter_parity_matrix.md` | Current or Snapshot | Check if still accurate |
| `adr_association_type_policy.md` | Current | ADRs should be kept; ensure listed in index |
| `bead_required_fields.md` | Current | Core schema reference; verify against models.py |
| `canonical_contract.md` | Current | Verify still accurate; link from index |
| `canonical_paths.md` | Current | Verify paths match post-Phase-9 layout |
| `public_surface.md` | Current | Keep |
| `claim_layer.md` | Current | Keep |
| `contributor_map.md` | Stale-but-useful | May reference old contributor patterns |
| `core_adapters_architecture.md` | Current or Superseded | Compare with `integrations/shared/` |
| `dreamer_contract.md` | Current | Keep; verify against runtime/dreamer/ |
| `good_first_issue_seed.md` | Current | Keep for contributors |
| `graph_memory.md` | Current | Keep; verify post-Phase-4 |
| `integration_contract.md` | Current | Verify against integrations/api.py after Phase 9g |
| `memory_surfaces_spec.md` | Current | Keep |
| `public_surface.md` | Current | Key doc; verify against __init__.__all__ |
| `reranker-paths.md` | Snapshot | Move to reports/ |
| `retrieval-canonical-v9-execution.md` | Snapshot | Move to reports/ or archive/ |
| `retrieval_kpi_targets.md` | Current or Snapshot | Check if still tracked |
| `retrieval_side_flow.md` | Current | Keep; verify post-Phase-5/6 |
| `runtime_contract_clarity.md` | Current | Keep; update for Phase-9 structure |
| `schema_canonical_spec.md` | Current | Keep |
| `schema_inventory_baseline.md` | Snapshot | Move to reports/ |
| `semantic_backend_modes.md` | Current | Keep; update for Phase-6 |
| `springai_adapter.md` | Superseded | Content is in `integrations/springai/` |
| `structural_pipeline_contract.md` | Current | Keep |
| `truth_hierarchy.md` | Current | Keep |
| `truth_hierarchy_policy.md` | Current | Keep |
| `write_side_artifacts_semantics.md` | Current | Keep |
| `write_side_flow.md` | Current | Keep |
| `ARCHITECTURE.md` | Superseded | Handled in 10b |

Files marked **Snapshot** or **Superseded** move to `docs/archive/` or `docs/reports/`
as appropriate. All **Current** files get verified and listed in `docs/index.md`.

---

## Historical sub-task 10e — Consolidate TODO tracking into `docs/status.md`

Before Phase 10, open-item tracking was split:
- `demo/TODO.md` — 7 engine-correctness items with cross-repo references
- `docs/reports/todo-validation-2026-05-15.md` — status audit against those 7 items
- `docs/cleanup-plan.md` — the new cleanup workstream (phases 0–8)

Phase 10 created `docs/status.md` as the single tracked-state document. The
current file is authoritative and should not be duplicated here; it now contains
the cleanup workstream closeout table, engine-correctness status, capability
items, open workstreams, and references.

`demo/TODO.md` retains its cross-repo references and notes but links to `docs/status.md`
for the authoritative completion state. `docs/reports/todo-validation-2026-05-15.md`
becomes a historical snapshot referenced from `docs/status.md`.

---

## Historical sub-task 10f — Add `docs/PRD/README.md`

Phase 10 added `docs/PRD/README.md` as the PRD index with one-line descriptions
and status. The current index is authoritative and covers cleanup PRDs,
capability PRDs, agency/self-model PRDs, active drafts, and deferred work.

---

## Historical sub-task 10g — Update `docs/index.md`

When 10g ran, `docs/index.md` needed:

1. **Architecture section** — remove `ARCHITECTURE.md` link; verify
   `architecture_overview.md` link is current.
2. **New sections:**
   - "Open workstreams" → links to `status.md` and `cleanup-plan.md`
   - "PRDs" → links to `PRD/README.md`
3. **Remove** links to files moved to archive/ in 10a and 10d.
4. **Add** links to any files classified as Current in 10d that aren't already listed.
5. **Verify** the `eval/` links at the bottom still resolve after Phase 9e
   (`longitudinal_benchmark.py` moved to `eval/`).
6. **Fix** the "Neo4j (shadow graph)" label in the Adapters section — after Phase 7, Neo4j
   is a query backend, not just a shadow graph.

---

## Guard rails

- **Do not edit `architecture_overview.md` until Phase 9 is complete.** The doc should
  reflect the actual post-refactor structure, not a future state.
- **Do not delete `demo/TODO.md`.** It has cross-repo references to `Core-Memory-Demo`
  that are meaningful to maintainers. Transform it to a pointer, not a replacement.
- **Each sub-task is a single PR.** Docs moves are small; PR per sub-task keeps the review
  surface narrow and the git blame clean.
- **The 10d classification audit requires reading each file.** Do not classify by filename
  alone — some files with historical-sounding names contain current contracts.
