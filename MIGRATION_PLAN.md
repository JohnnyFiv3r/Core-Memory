# Core Memory Migration Plan (`mem_beads` → `core_memory`)

Branch: `migrate-core-memory`

## Objective
Make `core_memory/` the canonical implementation while preserving behavior, safety, and deterministic outputs.

## Non-Negotiable Invariants
- [ ] Lossless storage (no destructive compaction side effects)
- [ ] Deterministic Context Packet assembly for same store+config
- [ ] Authored edges are canonical truth
- [ ] Derived edges are optional/pruneable
- [ ] Edge writes are lock-safe
- [ ] Existing `.mem-beads` data remains readable or migratable with backup

## Phases

## Phase 0 — Spec Freeze
- [ ] Finalize `COMPATIBILITY_SPEC.md`
- [x] Mark each command as: preserve / shim / deprecate (draft matrix added)
- [ ] Decide store strategy: read-compatible vs migrate-once

Deliverable: signed-off compatibility spec.

## Phase 1 — Parity Test Harness (before major code changes)
- [ ] Add command-level parity tests for:
  - add/query/update bead
  - link creation + traversal semantics
  - context packet deterministic output
  - compaction tier behavior
- [ ] Add concurrency/lock tests around edge writes
- [ ] Add env var compatibility tests (`MEMBEADS_ROOT`, `MEMBEADS_DIR`)
- [ ] Snapshot current `mem_beads` baseline outputs for key fixtures

Deliverable: failing tests that codify expected behavior.

## Phase 2 — Adapter Layer
- [ ] Keep current CLI entry (`mem_beads.cli:main`)
- [ ] Route internals to `core_memory` service/adapter
- [ ] Preserve argument names and output contracts where required
- [ ] Add compatibility shims for renamed concepts

Deliverable: tests green with CLI still exposed as `mem-beads`.

## Phase 3 — Store Compatibility
Choose one strategy:

### Option A: Read-Compatible (preferred)
- [ ] `core_memory` reads existing `.mem-beads` structures directly
- [ ] No conversion required for existing users

### Option B: One-Time Migration
- [ ] Add `mem-beads migrate-store` command
- [ ] Auto-backup before migration
- [ ] Transform + validate + checksum
- [ ] Write migration version marker

Deliverable: existing stores work safely without data corruption.

## Phase 4 — Canonical Package Flip
- [ ] Update `pyproject.toml` to canonical `core_memory` package
- [ ] Script entrypoint decision:
  - `core_memory.cli:main` (direct), or
  - keep `mem_beads.cli:main` as stable public entrypoint shim
- [ ] Ensure install/CI paths pass from repo root

Deliverable: `pip install -e .` + CLI + tests pass with `core_memory` canonical.

## Phase 5 — Cleanup / Deprecation
- [ ] Remove duplicated logic from `mem_beads`
- [ ] Keep thin compatibility wrappers where needed
- [ ] Mark removals for next major release if breaking
- [ ] Update docs/README/roadmap

Deliverable: single clear canonical implementation.

## Phase 6 — Release Readiness
- [ ] Full test suite green
- [ ] Migration smoke test on real snapshot data
- [ ] Changelog + release notes + breaking changes section
- [ ] Tag release candidate

Deliverable: merge-ready PR from `migrate-core-memory`.

---

## Risk Register
- **Schema drift risk**: `core_memory` model mismatch with existing store
  - Mitigation: spec + fixtures + migration validator
- **Silent behavior drift** (context packet ordering/tokening)
  - Mitigation: deterministic snapshot tests
- **Concurrency regression** (edge write corruption)
  - Mitigation: lock-focused tests + stress run
- **CLI UX breakage**
  - Mitigation: compatibility adapter + deprecation window

## Definition of Done
- [ ] All invariants preserved
- [ ] Compatibility spec fully addressed
- [ ] No unresolved TODOs in migration code path
- [ ] Maintainer sign-off on diff + behavior parity
