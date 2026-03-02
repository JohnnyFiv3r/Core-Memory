# Deprecation Plan: `mem_beads` → `core_memory`

## Status
In progress (post-canonical flip).

## Policy
- `core-memory` is the canonical CLI and package.
- `mem-beads` remains as a temporary command alias only.
- `mem_beads` Python module internals are deprecated.

## Schedule
- **Current release (N):**
  - `core-memory` default in docs/automation
  - `mem-beads` emits deprecation warning
  - no legacy runtime fallback in `mem_beads.cli`
- **Next release (N+1):**
  - reduce `mem_beads` package to minimal import shim + compatibility error messages
  - keep command alias if needed for operator convenience
- **Following release (N+2):**
  - remove `mem_beads` package internals entirely (optionally keep command alias wrapper)

## Migration guidance
1. Replace all automation with `core-memory` invocations.
2. For legacy stores, run:
   - `core-memory --root <new_root> migrate-store --legacy-root <legacy_root>`
3. Validate with:
   - query parity and count checks
   - compact/uncompact round-trip
   - idempotent second migration run

## Acceptance criteria for full deprecation
- no internal scripts reference `tools/mem-beads/*`
- no runtime path depends on legacy fallback
- migration drill documented and repeatable
- tests green on `master`
