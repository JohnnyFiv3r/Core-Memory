# V2-P7C Compatibility Usage Audit

Status: Step 2 artifact

## Scope
Audited compatibility/deprecated seams for direct usage references:
- `core_memory.memory_skill.form` (shim)
- `core_memory.write_pipeline.window` (shim)
- `process_pending_memory_events` legacy poller path
- HTTP compatibility ingress usage

## Findings

### 1) `core_memory.memory_skill.form`
- Still referenced by tests and compatibility paths.
- Primary path already switched to `core_memory.retrieval.search_form`.

Action:
- keep shim for now; no immediate removal in Step 3 unless callsites are all migrated.

### 2) `core_memory.write_pipeline.window`
- Still referenced by write-pipeline internals/tests.
- Primary canonical owner is now `core_memory.rolling_surface`.

Action:
- retain shim until internal callsites are fully migrated and parity validated.

### 3) `process_pending_memory_events`
- Retained as compatibility wrapper and hard-fenced by env flag.
- Used in legacy-path tests and explicit compatibility scenarios.

Action:
- keep wrapper; no authority semantics; maintain fence.

### 4) HTTP compatibility ingress
- SpringAI bridge exists and is primary framing.
- HTTP ingress remains compatibility implementation surface.

Action:
- keep compatibility ingress; continue bridge-first docs and examples.

## Migration map (Step 3 candidates)

### Candidate A (low risk)
- Migrate any remaining internal direct imports of `core_memory.memory_skill.form` to `core_memory.retrieval.search_form`.
- Keep shim module present but unused internally.

### Candidate B (low-medium risk)
- Migrate internal rolling calls to `core_memory.rolling_surface` directly.
- Keep `write_pipeline.window` shim for external compatibility.

### Candidate C (defer)
- Remove/retire legacy poller wrapper only after explicit operator confirmation and scenario coverage.

## Recommendation
Proceed with A+B in Step 3 (low-risk shim retirement of internal callsites), keep modules as compatibility shells for now.
