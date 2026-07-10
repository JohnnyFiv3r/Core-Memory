# Agent-Authored Turn Memory Rollout Playbook

Status: Hard-authorship cutover guidance

## Purpose
Provide a safe progression from deterministic fallback behavior to strict agent-authored enforcement.

## Control knob
- `CORE_MEMORY_AGENT_AUTHORED_MODE`
  - `hard` (shipping default)
  - `warn` (temporary compatibility mode)
  - `off` (legacy diagnostics only)

Resolved gate behavior:
- `hard` / legacy `enforce` => `required=true`, `fail_open=false`
- `warn` => `required=false`, `fail_open=true`
- `off` / legacy `observe` => `required=false`, `fail_open=true`

If mode is not set, hard mode is used. `CORE_MEMORY_AGENT_AUTHORED_REQUIRED=1`
remains a legacy way to force hard mode.

## Recommended rollout

### Compatibility diagnostics: Off
Set:
```bash
CORE_MEMORY_AGENT_AUTHORED_MODE=off
```
Use this phase to baseline:
- agent source rate
- fallback rate
- fail-closed rate (should be ~0 in observe)
- avg non-temporal semantic associations
- active shared_tag ratio

Command:
```bash
core-memory --root <root> graph association-slo-check
```

### Phase B: Warn
Set:
```bash
CORE_MEMORY_AGENT_AUTHORED_MODE=warn
```
Strict validation runs, but runtime can fail-open to fallback.
Use this to burn down malformed agent payloads before enforcement.

Command:
```bash
core-memory --root <root> graph association-slo-check --strict
```

### Production: Hard
Set:
```bash
CORE_MEMORY_AGENT_AUTHORED_MODE=hard
```
Missing/invalid agent-authored payloads write a durable pending-semantic record
and no canonical context bead.

Explicit full-contract repair is off by default. Enable it only when attributed
repair is intended:

```bash
CORE_MEMORY_AGENT_AUTHORED_REPAIR=1
```

Repair uses `turn_memory_authoring`, records primary and repair authorship
separately, and returns field-level provenance for changed fields.

## Suggested SLO thresholds (starter)
- min agent-authored rate: `0.80`
- max fallback rate: `0.10`
- max fail-closed rate: `0.25`
- min avg non-temporal semantic associations: `1.0`
- max active shared_tag ratio: `0.40`

Strict gate command:
```bash
core-memory --root <root> graph association-slo-check \
  --strict \
  --min-agent-authored-rate 0.80 \
  --max-fallback-rate 0.10 \
  --max-fail-closed-rate 0.25 \
  --min-avg-non-temporal-semantic 1.0 \
  --max-active-shared-tag-ratio 0.40
```

## Incident response
If hard mode produces a high pending-semantic rate (`ok=False` +
`semantic_status=pending|repair_required`; raw turn events remain durable):
1. Temporarily switch to `warn`
2. Inspect `agent_turn_quality` metrics + `error_code`
3. Fix callable/payload formatting
4. Re-run SLO check and return to enforce
5. Retry pending semantics with valid inline/delegated authorship; do not create
   deterministic stub beads
