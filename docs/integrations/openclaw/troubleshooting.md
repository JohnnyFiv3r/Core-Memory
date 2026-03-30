# OpenClaw Troubleshooting

Status: Canonical

## Common issues

### 1) Memory/runtime policy drift
Check:
- OpenClaw runtime instructions
- memory skill routing policy
- transcript-first vs memory-first semantics

### 2) Model allowlist/config mismatch
Symptoms:
- model accepted in config but runtime rejects it
- provider returns 404 or access errors

Check:
- effective runtime config
- model id correctness
- provider entitlement
- restart state

### 3) Compaction / memory flush surprises
Check:
- OpenClaw compaction prompt/config
- finalized-turn sidecar behavior
- extraction/consolidation scripts in use

If flush is unexpectedly skipped, verify runtime guards:
- `CORE_MEMORY_ENABLED` (bridge hard gate)
- `CORE_MEMORY_SUPERSEDE_OPENCLAW_SUMMARY` (coexist vs replace behavior)

### 4) Runtime retrieval disagreement
If transcript, beads, and grounded chains disagree:
- use transcript for immediate exact wording
- use memory graph for durable project truth
- surface ambiguity explicitly when needed

### 5) `agent_end emit failed` or timeout (`bridge_timeout:...`)
Common causes:
- plugin bridge timeout too low for current corpus/turn load
- session extraction failure collapsing writes into `session_id=main`

Checks:
- `/tmp/core-memory-bridge-hook.log` shows non-empty `agent_end session=...`
- bridge register line includes configured timeout (e.g. `bridgeTimeoutMs=60000`)
- no recurring `bridge_timeout:...:12000` after patch/restart

### 6) Transcript hydration returns nothing
Checks:
- `CORE_MEMORY_TRANSCRIPT_HYDRATION=1`
- `.turns/session-<id>.jsonl` exists (archive enabled at write time)
- `.turns/session-<id>.idx.json` has requested `turn_id`
- bead has `source_turn_ids` populated and `session_id` present
