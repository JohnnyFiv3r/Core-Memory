# V2-P17 Kickoff (Organizational Surface Cleanup)

Status: Active

## Objective
Improve repository organization and public OSS surface clarity without changing core runtime architecture.

## Step plan (4)
1. Docs archival pass ✅
2. sidecar/event replacement verification + cleanup ✅
3. integration docs folder normalization ✅
4. consolidate.py relocation safety gate

## Step 3 completion notes
- Treated `docs/integration/` as superseded by `docs/integrations/`.
- Archived legacy integration docs to:
  - `docs/archive/history/integration-legacy/`
- Removed active `docs/integration/` folder from main docs surface.
- Updated canonical/current references to point to `docs/integrations/shared/README.md`.

## Step 2 completion notes
- Updated runtime/test references to canonical `event_*` modules (`event_ingress`, `event_worker`, `event_state`).
- Verified public/current imports no longer depend on `sidecar_*` module names.
- Kept `sidecar_*` files as explicit transitional compatibility implementation layer for now because `event_*` currently delegates to them internally.
- Updated integration wording to event-runtime terminology in `core_memory/openclaw_integration.py`.

## Step 1 completion notes
- Archived non-canonical historical loose docs into `docs/archive/history/`.
- Moved phase/V2 migration trackers, closeouts, process roadmaps, and legacy report/problem docs out of main docs surface.
- Updated `docs/index.md` to point historical readers to archive paths while keeping canonical docs prominent.
