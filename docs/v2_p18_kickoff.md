# V2-P18 Kickoff (Event Runtime Ownership Cutover)

Status: Active

## Objective
Finalize event-runtime ownership by moving implementation authority to canonical `event_*` modules, removing legacy `sidecar_*` modules, and safely relocating consolidate implementation.

## Step plan (6)
1. Move implementation authority to `event_*` modules ✅
2. Global import migration + guardrails ✅
3. Remove `sidecar_*` files ✅
4. Move `consolidate.py` implementation to scripts path + root shim ✅
5. Update all consolidate references + remove root shim ✅
6. Sweep + closeout ✅

## Step 6 completion notes
- Completed V2P18 sweep across event runtime ownership, consolidate relocation, and e2e/runtime invariants.
- Added closeout artifact:
  - `docs/v2_p18_closeout_checklist.md`
- Sweep result: 20 passed / 0 failed.
- V2P18 is now closed.

## Step 1 completion notes
- Moved real implementation ownership into canonical modules:
  - `core_memory/event_state.py`
  - `core_memory/event_ingress.py`
  - `core_memory/event_worker.py`
- Converted legacy modules to compatibility-only shims.

## Step 2 completion notes
- Verified runtime imports now use canonical `event_*` modules after Step 1 ownership cutover.
- Expanded import guardrails to enforce sidecar-import absence in non-compat tests.

## Step 3 completion notes
- Removed legacy `sidecar_*` modules:
  - `core_memory/sidecar.py`
  - `core_memory/sidecar_hook.py`
  - `core_memory/sidecar_worker.py`
- Canonical `event_*` modules now fully own runtime implementation behavior.
- Updated import guardrails to enforce no sidecar module dependency in active runtime/tests.

## Step 4 completion notes
- Moved consolidation implementation to canonical script path:
  - `scripts/consolidate.py`
- Introduced temporary root compatibility shim for transition.

## Step 5 completion notes
- Updated active runtime/test/workflow references to canonical path:
  - `scripts/consolidate.py`
- Updated trigger dispatch to call `scripts/consolidate.py` directly.
- Removed root `consolidate.py` compatibility shim.
- Verified consolidate-path unit tests and enforcement matrix against new script location.
