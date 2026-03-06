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

### 4) Runtime retrieval disagreement
If transcript, beads, and grounded chains disagree:
- use transcript for immediate exact wording
- use memory graph for durable project truth
- surface ambiguity explicitly when needed
