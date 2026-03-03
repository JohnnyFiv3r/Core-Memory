# PR Summary — Core Memory Migration (Historical)

This file is retained as historical context for the migration effort.
It describes transitional branch strategy and intermediate compatibility decisions.

## Current canonical state
- Package/runtime: `core_memory`
- CLI: `core-memory` only
- Legacy `mem_beads` runtime path: removed
- Legacy `mem-beads` alias: removed
- Legacy stores: import with `core-memory migrate-store`

For current behavior and guarantees, use:
- `README.md`
- `COMPATIBILITY_SPEC.md`
- `DEPRECATION_PLAN.md`
- `CHANGELOG.md`
