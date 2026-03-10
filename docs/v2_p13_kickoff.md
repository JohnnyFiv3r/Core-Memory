# V2-P13 Kickoff (Single-Judgment Authority Cutover)

Status: Active

## Objective
Eliminate competing deterministic judgment authority so promotion/association decisions are canonical only through crawler-reviewed session-local outputs.

## Step plan (6)
1. Store association authority demotion ✅
2. Worker deterministic-judgment demotion ✅
3. Crawler-reviewed promotion/association authority enforcement ✅
4. pass_engine explicit non-primary deprecation lock
5. sidecar naming transition to event-* canonical terms
6. Sweep + closeout

## Step 1 completion notes
- Updated `core_memory.store.add_bead(...)` quick association path to preview-only.
- Store quick pass now writes non-authoritative `association_preview` hints only.
- Removed store-side canonical association append behavior from add path.
- Canonical association rows remain authored by crawler-reviewed apply/flush merge path.

## Step 2 completion notes
- Demoted deterministic worker promotion logic to non-authoritative preview mode in `core_memory.sidecar_worker`.
- Worker no longer mutates canonical promotion state via deterministic `store.promote(...)` path.
- Promotion candidates emitted by worker are explicitly marked `authoritative=false` for agent/crawler review.
- Candidate auto-archive/evaluation deterministic pass is disabled in canonical worker flow.

## Step 3 completion notes
- Added explicit authority-invariant coverage to enforce crawler-reviewed-only canonical mutation paths:
  - `tests/test_p13_authority_enforcement.py`
- Verified store add path no longer appends canonical association rows.
- Verified worker path no longer mutates canonical promotion state for seed/window beads.
- Canonical promotion/association mutation remains in crawler-reviewed apply + flush-merge path.
