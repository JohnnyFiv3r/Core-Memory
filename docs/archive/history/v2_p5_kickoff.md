# V2-P5 Kickoff

Status: Active
Related: `docs/v2_phase_ticket_map.md`, `docs/v2_deprecation_inventory.md`

## Objective
Finalize integration framing and legacy cleanup/deprecation resolution while preserving mainline stability.

## Step plan (5)
1. Integration framing inventory + target map ✅
2. SpringAI bridge framing cleanup (docs-first, low-risk aliases) ✅
3. Legacy path classification + explicit deprecation markers ✅
4. Canonical-path enforcement checks ✅
5. Full sweep + P5 closeout ✅

## Guardrails
- No silent contract drift for `memory.execute/search/reason`.
- Canonical path remains default authority.
- Deprecation before removal.
- Preserve operator fail-safe behavior.

## Step 1 completion notes
- Added integration surface inventory with as-is -> target mapping.
- Added path-level classification for canonical/compat/legacy semantics.
- Identified low-risk framing changes for Step 2 implementation.

## Step 2 completion notes
- Added SpringAI bridge package entrypoint:
  - `core_memory/integrations/springai/bridge.py`
  - `core_memory/integrations/springai/__init__.py`
- Bridge reuses canonical HTTP ingress while making SpringAI framing explicit.
- Updated SpringAI integration guide with bridge-first entrypoint notes.
- Added regression coverage: `tests/test_springai_bridge.py`

## Step 3 completion notes
- Updated deprecation inventory statuses in `docs/v2_deprecation_inventory.md`:
  - sidecar-authority path -> deprecated
  - duplicate wrapper authority paths -> deprecated
  - pre-v2 active-plan references -> deprecated
- Added explicit legacy/canonical classification artifact:
  - `docs/v2_p5_legacy_classification.md`
- Locked marker policy:
  - canonical -> `authority_path=canonical_in_process`
  - compat legacy -> `authority_path=legacy_sidecar_compat`

## Step 4 completion notes
- Added canonical-path enforcement test suite:
  - `tests/test_v2_p5_canonical_path_enforcement.py`
- Verifies:
  - canonical turn-finalized route remains default authority path
  - legacy poller path cannot double-process canonical-completed turns
  - core runtime response contract keys remain stable for `memory.execute`

## Step 5 completion notes
- Ran full regression sweep: `173 passed / 0 failed`
- Ran eval snapshots (`memory_execute_eval`, `paraphrase_eval`) and confirmed stability vs prior phase
- Authored closeout artifacts:
  - `docs/v2_p5_closeout_checklist.md`
  - `docs/v2_legacy_resolution_summary.md`
