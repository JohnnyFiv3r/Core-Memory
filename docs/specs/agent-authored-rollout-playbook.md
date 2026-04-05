# Agent-Authored Turn Memory Rollout Playbook

Status: Slice 7 operational guidance

## Purpose
Provide a safe progression from deterministic fallback behavior to strict agent-authored enforcement.

## Control knob
- `CORE_MEMORY_AGENT_AUTHORED_MODE`
  - `observe`
  - `warn`
  - `enforce`

Resolved gate behavior:
- `observe` => `required=false`, `fail_open=true`
- `warn` => `required=true`, `fail_open=true`
- `enforce` => `required=true`, `fail_open=false`

If mode is not set, legacy flags are used to derive equivalent behavior.

## Recommended rollout

### Phase A: Observe
Set:
```bash
CORE_MEMORY_AGENT_AUTHORED_MODE=observe
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

### Phase C: Enforce
Set:
```bash
CORE_MEMORY_AGENT_AUTHORED_MODE=enforce
```
Missing/invalid agent-authored payloads fail closed.
Use only after warn-phase SLOs are stable.

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
If enforce mode causes unacceptable turn blocking:
1. Temporarily switch to `warn`
2. Inspect `agent_turn_quality` metrics + `error_code`
3. Fix callable/payload formatting
4. Re-run SLO check and return to enforce
