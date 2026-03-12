# PR-01 Scaffolding Bootstrap Checklist

Date: 2026-03-12
Branch: feat/pr01-scaffolding-bootstrap

## Goal
Create import-safe package scaffolding for the streamlined architecture without behavior changes.

## Scope (No behavior changes)
- Add package namespaces:
  - `core_memory/runtime/`
  - `core_memory/persistence/`
  - `core_memory/memory_api/`
  - `core_memory/graph/`
  - `core_memory/legacy/`
- Add `__init__.py` files only, with explicit re-export comments.
- Add compatibility re-exports where needed (thin wrappers, no logic).
- Add architecture doc mapping old -> new import paths.

## Acceptance Criteria
1. Existing CLI commands behave identically.
2. Existing integration entry points still import cleanly.
3. No test expectation changes required (path-only compatibility).
4. New docs provide migration map for contributors.

## Planned Commit Sequence
1. `chore(structure): add runtime/persistence/memory_api package scaffolds`
2. `chore(compat): add no-op compatibility re-export modules`
3. `docs(architecture): add old-to-new import path map`

## Risk Controls
- Keep old modules untouched in this PR.
- No file moves yet; wrappers only.
- Validate with `python3 -m compileall -q core_memory`.

