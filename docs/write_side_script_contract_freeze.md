# Write-Side Script Contract Freeze (Phase 1)

Status: Canonical planning artifact
Scope: `extract-beads.py` and `consolidate.py`
Purpose: Freeze externally relied-on behavior before internalization refactor.

## Contract: extract-beads.py

### Entry path
- Root file path remains: `extract-beads.py`

### Invocation shapes to preserve
- `python3 extract-beads.py <session-id>`
- `python3 extract-beads.py --consolidate`
- `python3 extract-beads.py <session-id> --consolidate`

### Behavior invariants
- resolves transcript from explicit session id or latest-session fallback
- supports both marker syntaxes currently accepted
- validates bead payload against current type/scope/authority constraints
- writes beads through canonical Core Memory add path
- supports `--consolidate` chaining behavior

### Idempotency invariants
- extraction marker path remains:
  - `<CORE_MEMORY_ROOT>/.beads/.extracted/session-<id>.json`
- marker payload remains readable/backward-compatible
- extraction skip semantics remain controlled by current env behavior

## Contract: consolidate.py

### Entry path
- Root file path remains: `consolidate.py`

### Invocation shapes to preserve
- `python3 consolidate.py consolidate --session <id> [--promote]`
- `python3 consolidate.py rolling-window`

### Behavior invariants
- session-level compaction orchestration remains
- rolling/sliding window generation remains deterministic by recency policy
- historical compaction targeting remains after current window calculation
- safe default promotion behavior remains

### Artifact invariants
- rolling-window artifact path remains:
  - `promoted-context.md`
- output remains suitable for current downstream context injection usage

## Out-of-scope for this freeze
- no file relocation to `/scripts`
- no rename of root script entrypoints
- no artifact path migration in this phase
- no redesign of extraction marker storage path

## Usage
This freeze document must be reviewed before beginning write-side internalization work.
