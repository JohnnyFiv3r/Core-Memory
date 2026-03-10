# V2-P9 Kickoff (Session Purity + Bridge Semantics)

Status: Active

## Objective
Close remaining session-purity gaps by preserving real session boundaries in bridge sync, gating legacy projection fallbacks, and reducing continuity module coupling.

## Step plan (5)
1. Sidecar sync session semantics hardening ✅
2. Live-session fallback gating
3. Rolling continuity separation (selection vs render/write)
4. Regression and invariants
5. Full sweep + P9 closeout

## Step 1 completion notes
- Updated `scripts/sidecar_sync_session.py` to preserve real OpenClaw session IDs by default.
- Added explicit compatibility controls:
  - `--collapse-to-main` (legacy flattening mode)
  - `--core-session-id` (explicit override)
- Output now reports both:
  - `openclaw_session_id`
  - `core_session_id`
- Added regression coverage in `tests/test_sidecar_sync_session_semantics.py`.
