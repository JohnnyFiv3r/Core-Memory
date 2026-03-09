# V2-P8B Kickoff (Continuity Surface Purification)

Status: Active

## Objective
Eliminate ambiguity in continuity read/write authority so runtime continuity injection is deterministic and all non-authoritative continuity artifacts are explicitly demoted.

## Step plan (5)
1. Surface authority contract hardening
2. Derived artifact demotion + metadata normalization ✅
3. Read-path purification sweep

## Step 2 completion notes
- Normalized continuity metadata semantics across authority and derived surfaces.
- `rolling-window.records.json` now explicitly tags:
  - `authority=rolling_record_store`
  - `role=runtime_continuity_authority`
- `promoted-context.meta.json` now explicitly tags derived fallback role:
  - `authority=promoted_context_meta_fallback`
  - `role=derived_fallback_metadata`
- `promoted-context.md` remains derived/operator-facing only.
- Updated OpenClaw integration guidance to state continuity authority/fallback split.
4. Regression and invariants
5. Full sweep + P8B closeout

## Step 1 completion notes
- Added canonical continuity authority contract alignment across docs and runtime loader language.
- Established explicit continuity authority order:
  1) `rolling-window.records.json` (authoritative)
  2) `promoted-context.meta.json` (fallback metadata only)
  3) empty
- Clarified that `promoted-context.md` is derived/operator-facing and never runtime authority.
