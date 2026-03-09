# V2-P4 Kickoff

Status: Active
Related: `docs/v2_phase_ticket_map.md`, `docs/v2_gap_checklist.md`

## Objective
Close surface and schema embodiment gaps:
- rolling surface as first-class continuity store contract
- association subsystem extraction
- schema/model reconciliation
- association bead-type decision closure

## Step plan (5)
1. Rolling window first-class surface contract hardening
2. Rolling FIFO/token-budget determinism test expansion
3. Association subsystem extraction scaffold
4. Schema reconciliation (`models.py` aligned to canonical `schema.py`)
5. Association bead-type decision closure + P4 closeout

## Planned outputs
- `docs/v2_p4_surface_contract.md`
- `docs/v2_p4_test_matrix.md`
- `core_memory/association/` scaffold (step 3)
- schema reconciliation diff + migration note (step 4)
- ADR/decision note for association bead-type resolution (step 5)

## Guardrails
- Preserve mainline path stability at each step.
- No silent contract drift in `memory.execute/search/reason`.
- Keep compatibility wrappers until explicit deprecation step.
