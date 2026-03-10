# V2-P9 Closeout Checklist

Status: Complete

## Scope
Session Purity + Bridge Semantics closure:
- sidecar sync session boundary correctness
- live-session fallback gating
- rolling continuity separation
- session-purity invariants

## Completion checklist
- [x] Sidecar sync preserves real session IDs by default
- [x] Compatibility collapse mode is explicit/opt-in
- [x] Live-session index fallback gated behind explicit env flag
- [x] Rolling continuity internals separated (selection/render/write)
- [x] Session purity invariants added and passing
- [x] Step 5 sweep completed

## Regression evidence
Command:

```bash
python3 -m unittest \
  tests.test_sidecar_sync_session_semantics \
  tests.test_live_session_authority \
  tests.test_rolling_surface_contract \
  tests.test_rolling_surface_owner \
  tests.test_rolling_surface_separation \
  tests.test_p9_session_purity_invariants \
  tests.test_memory_engine \
  tests.test_session_first_write_authority -v
```

Result:
- 16 passed / 0 failed

## Final session-purity stance
- Bridge sync default: preserve resolved OpenClaw session IDs
- Compatibility flattening: opt-in via `--collapse-to-main`
- Live session authority default: session surface only
- Index fallback: compatibility-only via `CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK=1`
