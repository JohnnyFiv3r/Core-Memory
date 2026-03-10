# V2-P11 Kickoff (Transcript Index-Dump Retirement)

Status: Active

## Objective
Retire transcript/index-dump as a supported primary write architecture and lock canonical write authority to finalized-turn event/session-first flow.

## Step plan (4)
1. Decision lock + canonical docs update ✅
2. Code removal of transcript dump path ✅
3. Bridge-only clarification and residual cleanup ✅
4. Sweep + closeout ✅

## Step 4 completion notes
- Completed V2P11 regression sweep.
- Added closeout artifact:
  - `docs/v2_p11_closeout_checklist.md`
- Sweep result: 25 passed / 0 failed.
- V2P11 is now closed.

## Step 1 completion notes
- Locked architectural decision in canonical/integration docs:
  - transcript/index-dump is retired as primary write path
  - transcript inputs are bridge-only into canonical finalized-turn ingestion
- Updated canonical surfaces/paths references to match current repo shape and archive layout.

## Step 2 completion notes
- Removed transcript/index-dump primary write-path files:
  - `extract-beads.py`
  - `core_memory/write_pipeline/transcript_source.py`
  - `core_memory/write_pipeline/marker_parse.py`
  - `core_memory/write_pipeline/normalize.py`
  - `core_memory/write_pipeline/persist.py`
  - `core_memory/write_pipeline/idempotency.py`
- Removed extraction parity test:
  - `tests/test_write_pipeline_extract_parity.py`
- Updated orchestration exports to remove extract pipeline surface.
- `extract_beads` write-trigger dispatch is now explicit retired path (`error=extract_path_retired`).

## Step 3 completion notes
- Clarified transcript bridge semantics in `docs/integration/memory-sidecar.md`:
  - transcript input is bridge-only
  - no transcript/index-dump primary write path
  - bridge feeds canonical finalized-turn event/session-first flow
- Retained `scripts/sidecar_sync_session.py` as bridge utility only while native runtime finalize wiring maturity varies by deployment.
