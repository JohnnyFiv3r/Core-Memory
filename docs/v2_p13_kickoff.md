# V2-P13 Kickoff (Single-Judgment Authority Cutover)

Status: Active

## Objective
Eliminate competing deterministic judgment authority so promotion/association decisions are canonical only through crawler-reviewed session-local outputs.

## Step plan (6)
1. Store association authority demotion ✅
2. Worker deterministic-judgment demotion
3. Crawler-reviewed promotion/association authority enforcement
4. pass_engine explicit non-primary deprecation lock
5. sidecar naming transition to event-* canonical terms
6. Sweep + closeout

## Step 1 completion notes
- Updated `core_memory.store.add_bead(...)` quick association path to preview-only.
- Store quick pass now writes non-authoritative `association_preview` hints only.
- Removed store-side canonical association append behavior from add path.
- Canonical association rows remain authored by crawler-reviewed apply/flush merge path.
