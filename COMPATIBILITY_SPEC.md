# Compatibility Spec: `mem_beads` → `core_memory`

Status: Draft (to finalize before migration code changes)

## 1) Public CLI Contract
Current public command: `mem-beads`

Decision (recommended): keep command name stable (`mem-beads`) while swapping internals.

### Command Compatibility Matrix
| Command Area | Current Behavior | Required During Migration | Notes |
|---|---|---|---|
| Add/Create bead | Structured bead creation | Preserve | Same required fields and defaults |
| Query/list/filter | Read by type/status/session/tags | Preserve | Ordering must remain deterministic |
| Link/edges | Authored edge creation | Preserve | Keep edge direction semantics |
| Context packet | Budgeted deterministic selection | Preserve | Must match deterministic ordering rules |
| Compaction | Tiered render behavior | Preserve | Store remains lossless |
| Myelination | Derived-edge-only reinforcement/pruning | Preserve | Never mutates authored truth |
| Migration command | N/A or ad-hoc | Add if needed | `migrate-store` only if schema changes |

## 2) Data Store Contract
Store root: `.mem-beads/` (via `MEMBEADS_ROOT`; fallback `MEMBEADS_DIR`)

Expected artifacts:
- `index.json`
- `global.jsonl`
- `edges.jsonl`
- `sessions/session-<id>.jsonl` (or equivalent session files)

### Compatibility Requirements
- Existing stores must either:
  1. work read/write as-is, or
  2. be migrated by explicit command with backup + validation.

- No silent destructive conversion.

## 3) Edge Semantics Contract
- Edge direction must remain consistent with current project rule.
- Authored edges: immutable canonical causal truth.
- Derived edges: optional and pruneable.
- Edge rewrites deterministic (sorted where applicable).
- All edge writes protected by lock.

## 4) Determinism Contract
For same store + config + budget:
- Same bead selection
- Same ordering
- Same rendered packet structure

Tests must snapshot and compare outputs.

## 5) Environment/Config Contract
- `MEMBEADS_ROOT` stays primary.
- `MEMBEADS_DIR` compatibility shim stays during migration window.
- CLI defaults unchanged unless explicitly documented as breaking.

## 6) Packaging Contract
Current package root: repo root.

Target options:
1. **Direct canonical**: `core_memory` package exported, CLI points to `core_memory.cli:main`
2. **Stability-first**: keep script entrypoint `mem_beads.cli:main` as thin shim delegating to `core_memory`

Recommendation: option 2 for one release cycle to reduce breakage.

## 7) Backward Compatibility Window
- Keep shims for at least one minor release.
- Emit deprecation warnings for renamed/internal APIs.
- Document removal schedule in README/CHANGELOG.

## 8) Validation Checklist (must pass before merge)
- [ ] CLI parity tests green
- [ ] Edge lock/concurrency tests green
- [ ] Deterministic packet snapshots green
- [ ] Real store migration/read smoke test green
- [ ] No authored-edge mutation by myelination
- [ ] Install + run from clean env works (`pip install -e .`)

## 9) Open Questions
- [x] Is edge direction currently documented unambiguously in README + tests?
  - Status: yes in tests; README wording should be tightened in final docs pass.
- [ ] Should sessions remain file-based or move fully index/event first now?
- [ ] Do we need explicit schema versioning in `index.json` before migration?
- [ ] Keep `core_memory/` and `mem_beads/` public, or only one public import path?

## 10) Phase 2 Command Mapping Status (current branch)
Legend: ✅ direct in core adapter | 🟡 translated to core CLI | 🔁 legacy fallback

- `create` → 🟡 translated to core `add`
- `add` → 🟡 core CLI native
- `query` → 🟡 translated/core-compatible
- `stats` → 🟡 core CLI native
- `rebuild-index` → 🟡 translated to core `rebuild`
- `link` → ✅ direct handler (`MemoryStore.link`)
- `recall` → ✅ direct handler (`MemoryStore.recall`)
- `supersede` → ✅ direct handler (creates `supersedes` association)
- `validate` → ✅ direct handler (compat payload)
- `close --status promoted` → ✅ direct handler (`MemoryStore.promote`)
- `close` (other statuses) → 🔁 fallback to legacy
- `compact` → 🔁 fallback to legacy
- `uncompact` → 🔁 fallback to legacy
- `myelinate` → 🔁 fallback to legacy

## 11) Decision Gates (resolved)
1. **Compaction path:**
   - ✅ Implement core-native `compact` / `uncompact` / `myelinate`

2. **Store strategy:**
   - ✅ Add explicit `migrate-store` command for legacy memory migration

3. **Public import path policy:**
   - ✅ Canonical flip to `core_memory` now (with `mem-beads` command alias retained for operator convenience)
