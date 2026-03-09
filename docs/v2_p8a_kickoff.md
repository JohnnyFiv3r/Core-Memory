# V2-P8A Kickoff (Runtime/State Authority Cutover)

Status: Active

## Step plan (5)
1. Move ordered turn+flush sequencing into `memory_engine.py` ✅
2. Reduce `trigger_orchestrator.py` to thin helper compatibility layer
3. Shift crawler-applied updates to session-local side logs ✅
4. Flush merge path: session beads + promotions + associations -> archive/projection ✅
5. Full sweep + P8A closeout

## Step 4 completion notes
- Added flush-merge path for session-local crawler side logs:
  - `core_memory.association.crawler_contract.merge_crawler_updates_for_flush(...)`
- `memory_engine.process_flush(...)` now runs crawler side-log merge before consolidate pipeline.
- Merged updates are applied into index projection at flush-time and consumed side-log entries are cleared.
- Flush result envelope now reports merge outcome under `crawler_merge`.
- Added/updated regression assertions for queue -> flush-merge behavior.

## Step 3 completion notes
- Updated `core_memory.association.crawler_contract.apply_crawler_updates(...)` to stop mutating `index.json` directly.
- Crawler judgments are now validated and queued into a session-local side log:
  - `.beads/events/crawler-updates-<session_id>.jsonl`
- Side-log rows are append-only and typed:
  - `kind=promotion_mark`
  - `kind=association_append`
- Return envelope now marks side-log authority:
  - `authority_path=session_side_log`
- Updated regression coverage to assert side-log writes and non-mutation of `index.json` at apply time.

## Step 1 completion notes
- `memory_engine.process_turn_finalized(...)` now owns ordered turn sequencing directly:
  - emit finalized event
  - locate event row
  - idempotent claim
  - process memory event
  - return canonical engine-owned result envelope
- `memory_engine.process_flush(...)` now owns ordered flush sequencing directly:
  - live-session preflight snapshot
  - enrichment barrier validation
  - consolidate pipeline execution
  - canonical engine-owned result envelope
- This reduces reliance on `trigger_orchestrator.run_*` sequencing ownership.

## Step 2 completion notes
- Replaced `core_memory/trigger_orchestrator.py` implementation with thin compatibility wrappers.
- `run_turn_finalize_pipeline(...)` and `run_flush_pipeline(...)` now delegate to `core_memory.memory_engine`.
- Added explicit shim markers in trigger orchestrator module:
  - `LEGACY_SHIM=True`
  - `SHIM_REPLACEMENT=core_memory.memory_engine`
- Updated trigger-orchestrator regression tests to validate delegation behavior.
