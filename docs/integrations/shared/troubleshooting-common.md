# Shared Troubleshooting

Status: Canonical

## Common issues across integrations

### 1) Empty or weak retrieval
Check:
- `warnings`
- `grounding.reason`
- `confidence`
- `next_action`
- explain/debug artifacts

### 2) Model mismatch / access errors
Symptoms:
- provider 404 model errors
- allowlist accepts a model but provider denies it

Check:
- provider model id
- account entitlement
- agent allowlist/default model settings
- restart after config updates

### 3) Runtime/config drift
Symptoms:
- config file looks correct but runtime behaves differently

Check:
- effective runtime config
- restart state
- whether another config file or deployment artifact is actually being loaded

### 4) Idempotency issues on write path
Check:
- stable `session_id`
- stable `turn_id`
- repeated finalized-turn submissions
- `memory-pass-state.json`

### 5) Transcript vs memory disagreement
Policy guideline:
- prefer transcript for immediate/verbatim recent-turn truth
- prefer memory graph for durable cross-session/project decisions
- note disagreement explicitly when material
