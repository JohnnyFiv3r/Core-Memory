# Phase 1 Closeout Checklist

Status: Canonical transition checklist
Related roadmap: `docs/transition_roadmap_locked.md`

## Goal
Confirm Phase 1 (canonicalization + transition readiness) is complete before Phase T1 implementation work begins.

## Checklist

### Canonical docs/navigation
- [x] `docs/index.md` exists and points to canonical integration docs
- [x] `docs/canonical_surfaces.md` exists and reflects current canonical surfaces
- [x] status labels added for key docs (canonical/supporting/transitional/historical)

### Integration docs structure
- [x] `docs/integrations/shared/` v1 docs exist
- [x] `docs/integrations/springai/` v1 docs exist
- [x] `docs/integrations/openclaw/` v1 docs exist
- [x] `docs/integrations/pydanticai/` v1 docs exist
- [x] `docs/springai_adapter.md` converted to transitional stub

### Contract and parity
- [x] machine-readable contract exists: `docs/contracts/http_api.v1.json`
- [x] runtime endpoint tests pass (`tests.test_http_ingress`)
- [x] execute/reason contract tests pass

### Write-side transition readiness
- [x] write-side pipeline map exists: `docs/write_side_pipeline_map.md`
- [x] write-side script contract freeze exists: `docs/write_side_script_contract_freeze.md`
- [x] write-side event model gap baseline exists: `docs/write_side_event_model_gap_baseline.md`

### Historical artifact hygiene
- [x] dated report docs labeled historical snapshots

## Exit criteria
Phase 1 is considered complete when this checklist remains true on `master` and no unresolved canonical-surface ambiguity is identified.

## Next phase
Proceed to Phase T1:
- schema normalization (bead types / edge types / state flags separation)
