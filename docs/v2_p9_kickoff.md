# V2-P9 Kickoff (Session Purity + Bridge Semantics)

Status: Active

## Objective
Close remaining session-purity gaps by preserving real session boundaries in bridge sync, gating legacy projection fallbacks, and reducing continuity module coupling.

## Step plan (5)
1. Sidecar sync session semantics hardening ✅
2. Live-session fallback gating ✅
3. Rolling continuity separation (selection vs render/write)

## Step 2 completion notes
- Added explicit gating for live-session index fallback in `core_memory.live_session`.
- New default is strict session-surface authority:
  - when session surface is empty and fallback disabled -> `authority=session_surface_empty`
- Compatibility fallback remains available behind env flag:
  - `CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK=1`
- Updated live-session authority tests to cover both strict-default and opt-in fallback modes.
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
