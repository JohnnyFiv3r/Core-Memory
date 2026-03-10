# V2-P11 Kickoff (Transcript Index-Dump Retirement)

Status: Active

## Objective
Retire transcript/index-dump as a supported primary write architecture and lock canonical write authority to finalized-turn event/session-first flow.

## Step plan (4)
1. Decision lock + canonical docs update ✅
2. Code removal of transcript dump path
3. Bridge-only clarification and residual cleanup
4. Sweep + closeout

## Step 1 completion notes
- Locked architectural decision in canonical/integration docs:
  - transcript/index-dump is retired as primary write path
  - transcript inputs are bridge-only into canonical finalized-turn ingestion
- Updated canonical surfaces/paths references to match current repo shape and archive layout.
