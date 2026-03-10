# V2-P11 Kickoff (Transcript Index-Dump Retirement)

Status: Active

## Objective
Retire transcript/index-dump as a supported primary write architecture and lock canonical write authority to finalized-turn event/session-first flow.

## Step plan (4)
1. Decision lock + canonical docs update ✅
2. Code removal of transcript dump path ✅
3. Bridge-only clarification and residual cleanup

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
4. Sweep + closeout

## Step 1 completion notes
- Locked architectural decision in canonical/integration docs:
  - transcript/index-dump is retired as primary write path
  - transcript inputs are bridge-only into canonical finalized-turn ingestion
- Updated canonical surfaces/paths references to match current repo shape and archive layout.
