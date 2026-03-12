# V2 P9 Kickoff

## Step plan

1. Preserve OpenClaw session identity as default Core Memory session target.
2. Allow explicit compatibility collapse to `main` only when requested.
3. Verify strict/compat read authority behavior with tests.

## Step 3 completion notes

- Session purity invariants are covered by dedicated tests.
- Bridge default preserves source session ids.
- Collapse mode remains explicit and opt-in.
