# Deprecation Plan: `mem_beads` → `core_memory`

## Status
Completed.

## Final Policy (current)
- `core-memory` is the only supported CLI.
- Legacy `mem-beads` command alias has been removed.
- Legacy `mem_beads` runtime module path has been removed.
- Legacy stores can be imported with:
  - `core-memory --root <new_root> migrate-store --legacy-root <legacy_root>`

## Compatibility Notes
- Environment compatibility inputs are still accepted where relevant:
  - `MEMBEADS_ROOT`
  - `MEMBEADS_DIR`
- Preferred environment variable is:
  - `CORE_MEMORY_ROOT`

## Acceptance criteria (met)
- no runtime code depends on `mem_beads`
- canonical package path is `core_memory`
- canonical CLI is `core-memory`
- migration flow is explicit and tested (`migrate-store`)
