# V2-P17 Step 4 — consolidate.py relocation safety gate

Status: Gate failed (no relocation performed)

## Gate rule
Stop and warn if all references to `consolidate.py` cannot be safely updated in one atomic pass.

## Inventory summary (non-archive)
Relocation currently touches multiple active surfaces:
- Runtime/workflow docs:
  - `WORKFLOW_AUTO.md`
- Tests:
  - `tests/test_v2_p2_enforcement_matrix.py`
  - `tests/test_write_trigger_dispatch.py`
- Plans/skills:
  - `plans/workflow-auto-core-memory-plan.md`
  - `skills/mem-beads/SKILL.md`

Additional historical references exist in memory artifacts and archived docs.

## Decision
- **Do not move `consolidate.py` in V2P17.**
- Keep root `consolidate.py` as thin operational wrapper.
- Queue relocation as a dedicated future pass with explicit reference migration plan.

## Rationale
Attempting relocation now would couple runtime, tests, workflow docs, and skills updates in a broad cross-surface change and violates the one-pass safety gate for this organizational cleanup step.
