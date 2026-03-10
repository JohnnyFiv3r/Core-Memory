# V2-P19 Kickoff (Turn-to-Retrieval Contract Restore)

Status: Active

## Objective
Restore canonical end-to-end behavior so finalized turns produce semantic beads through crawler-reviewed authority and become available for rolling continuity + retrieval.

## Step plan (3)
1. Failing-first contract tests ✅
2. Implement canonical semantic creation path
3. Sweep + closeout

## Step 1 completion notes
- Added failing-first contract suite:
  - `tests/test_v2p19_turn_to_retrieval_contract.py`
- Baseline failure confirms current gap:
  - turn processing does not currently append semantic bead(s)
  - flush does not include new turn record in rolling window
  - retrieval cannot find newly created turn memory
- This baseline is intentional to drive Step 2 implementation.
