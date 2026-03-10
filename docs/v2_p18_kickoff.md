# V2-P18 Kickoff (Event Runtime Ownership Cutover)

Status: Active

## Objective
Finalize event-runtime ownership by moving implementation authority to canonical `event_*` modules and reducing `sidecar_*` to temporary compatibility shims.

## Step plan (6)
1. Move implementation authority to `event_*` modules ✅
2. Global import migration + guardrails ✅
3. Remove `sidecar_*` files

## Step 2 completion notes
- Verified runtime imports now use canonical `event_*` modules after Step 1 ownership cutover.
- Expanded import guardrails to enforce sidecar-import absence in non-compat tests.
- Kept sidecar-specific tests isolated as transition compatibility coverage only.
4. Move `consolidate.py` implementation to scripts path + root shim
5. Update all consolidate references + remove root shim
6. Sweep + closeout

## Step 1 completion notes
- Moved real implementation ownership into canonical modules:
  - `core_memory/event_state.py`
  - `core_memory/event_ingress.py`
  - `core_memory/event_worker.py`
- Converted legacy modules to compatibility-only shims:
  - `core_memory/sidecar.py`
  - `core_memory/sidecar_hook.py`
  - `core_memory/sidecar_worker.py`
- Verified sidecar/event contracts and engine integration remain green.
