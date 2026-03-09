# V2-P5 Kickoff

Status: Active
Related: `docs/v2_phase_ticket_map.md`, `docs/v2_deprecation_inventory.md`

## Objective
Finalize integration framing and legacy cleanup/deprecation resolution while preserving mainline stability.

## Step plan (5)
1. Integration framing inventory + target map
2. SpringAI bridge framing cleanup (docs-first, low-risk aliases)
3. Legacy path classification + explicit deprecation markers
4. Canonical-path enforcement checks
5. Full sweep + P5 closeout

## Guardrails
- No silent contract drift for `memory.execute/search/reason`.
- Canonical path remains default authority.
- Deprecation before removal.
- Preserve operator fail-safe behavior.

## Step 1 completion notes
- Added integration surface inventory with as-is -> target mapping.
- Added path-level classification for canonical/compat/legacy semantics.
- Identified low-risk framing changes for Step 2 implementation.
