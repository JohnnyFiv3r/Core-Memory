# V2-P8B Kickoff (Continuity Surface Purification)

Status: Active

## Objective
Eliminate ambiguity in continuity read/write authority so runtime continuity injection is deterministic and all non-authoritative continuity artifacts are explicitly demoted.

## Step plan (5)
1. Surface authority contract hardening
2. Derived artifact demotion + metadata normalization
3. Read-path purification sweep
4. Regression and invariants
5. Full sweep + P8B closeout

## Step 1 completion notes
- Added canonical continuity authority contract alignment across docs and runtime loader language.
- Established explicit continuity authority order:
  1) `rolling-window.records.json` (authoritative)
  2) `promoted-context.meta.json` (fallback metadata only)
  3) empty
- Clarified that `promoted-context.md` is derived/operator-facing and never runtime authority.
