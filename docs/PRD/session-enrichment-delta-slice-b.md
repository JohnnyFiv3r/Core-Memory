# PRD: Session Enrichment Delta — Slice B

**Status:** Implemented
**Target file:** `core_memory/runtime/passes/enrichment.py`  
**Effort:** Historical estimate
**Depends on:** Slice A (done)  
**Mandated by:** `docs/PRD/session-enrichment-delta-analysis.md` Slice B design rationale

---

## Current implementation note

Slice B is implemented in `core_memory.runtime.passes.enrichment`.
`run_turn_enrichment()` now accepts `enrichment_run_id`, auto-generates one when
absent, persists a `session_enrichment_delta.v1` envelope under
`.beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl`, returns cached
`stage_results` for repeated runs with the same id, and exposes all nine
`stage_results` keys. The queued side-effect path in
`core_memory.runtime.queue.side_effect_queue` supplies an `enrichment_run_id`
when processing `turn-enrichment` jobs.

The implementation tasks below are retained as historical context for what
shipped.

---

## Historical problem

At the time this PRD was written, the enrichment pipeline
(`run_turn_enrichment`) ran up to 9 stages per turn but lacked a durable record
of a completed run. If the same turn was re-enriched (agent retry, job replay,
crash recovery), all 9 stages re-executed and could produce duplicate
associations, duplicate claim updates, and duplicate goal lifecycle transitions
that were not fully captured by per-row dedupe keys.

Stage 4 (crawler merge + log clear) also needed an atomicity guarantee: a crash
between the merge write and the log clear could leave the pipeline in an
inconsistent state that subsequent runs could not recover from deterministically.

---

## User value

- Agent retries and job replays are safe — re-running enrichment on the same turn
  produces identical state, not duplicates.
- Enrichment runs are inspectable: every completed run persists a structured delta
  envelope with per-stage metrics, timestamps, and an idempotency token.
- Crash recovery is deterministic: Stage 4 atomicity removes the inconsistent
  intermediate state.

---

## Current implementation state

| Component | Status |
|-----------|--------|
| `run_turn_enrichment()` in `enrichment.py` | Done — runs all 9 stages |
| `enrichment_run_id` parameter | Shipped — caller-supplied or auto-generated |
| Stage 4 atomic lock | Shipped through the atomic crawler merge path |
| Delta envelope persistence | Shipped — `.beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl` |
| Idempotency gate | Shipped — repeated `(bead_id, enrichment_run_id)` calls return cached `stage_results` |

---

## Success criteria

These criteria are implemented and covered by `tests/test_enrichment_slice_b.py`.

1. Calling `run_turn_enrichment()` twice with the same `enrichment_run_id` returns
   the cached result on the second call — no stages re-execute, no duplicates.
2. A `.beads/events/enrichment-{bead_id}-{run_id_prefix}.jsonl` file is written after
   every successful enrichment run containing the delta envelope defined below.
3. Stage 4 (crawler merge + log clear) executes under `store_lock` — a crash during
   Stage 4 leaves no half-applied state.
4. Canonical projection equality holds: a turn enriched inline and the same turn
   enriched via the queued job path produce byte-for-byte equivalent canonical state.
5. All 9 `stage_results` keys are present in the persisted envelope, even if a stage
   produced zero rows.

---

## Scope

**In:**
- `enrichment_run_id: str` parameter added to `run_turn_enrichment()` (UUID, caller-supplied)
- Idempotency gate at entry: check `.beads/events/` for an existing envelope matching
  `(bead_id, enrichment_run_id)`; if found, return cached `stage_results` immediately
- Stage 4 atomic wrap: move crawler merge + log clear inside a single `store_lock` block
- Delta envelope schema (see below) persisted to
  `.beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl`
- `idempotency_token`: `grounding_hash(bead_id + enrichment_run_id)`

**Out:**
- Changes to any stage's internal logic — this slice is plumbing only
- New row types in the delta envelope (claims, goal_lifecycle, memory_outcomes remain
  reserved/diagnostic-only as per Slice A contract)
- Backfilling existing turns with delta envelopes
- Exposing `enrichment_run_id` on the public API surface (`recall`, `emit_turn_finalized`)

---

## Delta envelope schema

```json
{
  "schema": "session_enrichment_delta.v1",
  "bead_id": "<canonical turn bead id>",
  "session_id": "<session id>",
  "turn_id": "<turn id>",
  "enrichment_run_id": "<uuid>",
  "triggered_at": "<iso8601>",
  "completed_at": "<iso8601>",
  "idempotency_token": "<grounding_hash(bead_id + enrichment_run_id)>",
  "stages_run": [
    "association_pass", "claim_extraction", "preview_associations",
    "crawler_merge", "decision_pass", "claim_updates",
    "memory_outcome", "goal_lifecycle", "quality_metric"
  ],
  "stage_results": {
    "association_pass":     { "queued": 0, "skipped_existing": 0 },
    "claim_extraction":     { "extracted": 0, "skipped_duplicate": 0 },
    "preview_associations": { "queued": 0, "skipped_existing": 0 },
    "crawler_merge":        { "merged": 0, "quarantined": 0 },
    "decision_pass":        { "emitted": 0, "skipped_grounding": 0 },
    "claim_updates":        { "emitted": 0, "skipped_grounding": 0 },
    "memory_outcome":       { "written": false },
    "goal_lifecycle":       { "transitioned": 0 },
    "quality_metric":       { "score": null }
  }
}
```

The envelope is appended as a single JSON line to
`.beads/events/enrichment-{bead_id}-{run_id[:8]}.jsonl`. One file per (bead, run).

---

## Implementation tasks

### 1. `core_memory/runtime/passes/enrichment.py` — add `enrichment_run_id` parameter

Add `enrichment_run_id: str | None = None` to `run_turn_enrichment()`. If the caller
does not supply one, generate a UUID at entry (`uuid.uuid4().hex`).

### 2. `enrichment.py` — idempotency gate

At the top of `run_turn_enrichment()`, before any stage executes:

```python
envelope_path = _enrichment_envelope_path(root, bead_id, enrichment_run_id)
if envelope_path.exists():
    cached = json.loads(envelope_path.read_text().strip().splitlines()[-1])
    return {"idempotent": True, "stage_results": cached["stage_results"]}
```

`_enrichment_envelope_path(root, bead_id, run_id)` returns:
`Path(root) / ".beads" / "events" / f"enrichment-{bead_id}-{run_id[:8]}.jsonl"`

### 3. `enrichment.py` — Stage 4 atomic wrap

Locate the crawler merge + log clear sequence (Stage 4). Wrap both writes inside
a single `with store_lock(Path(root)):` block. The log clear must happen inside
the same lock acquisition as the merge write. No other stage logic changes.

### 4. `enrichment.py` — stage_results accumulation

Thread a `stage_results: dict` through the run. Each stage appends its counts to
`stage_results[stage_name]` after completing. Use the schema keys above. Counts
increment atomically within the run; they are not read back from storage.

### 5. `enrichment.py` — envelope persistence

After all 9 stages complete (and before returning), write the delta envelope:

```python
envelope = {
    "schema": "session_enrichment_delta.v1",
    "bead_id": bead_id,
    "session_id": session_id,
    "turn_id": turn_id,
    "enrichment_run_id": enrichment_run_id,
    "triggered_at": triggered_at_iso,
    "completed_at": datetime.now(UTC).isoformat(),
    "idempotency_token": _grounding_hash(bead_id + enrichment_run_id),
    "stages_run": list(stage_results.keys()),
    "stage_results": stage_results,
}
envelope_path.parent.mkdir(parents=True, exist_ok=True)
with envelope_path.open("a") as f:
    f.write(json.dumps(envelope) + "\n")
```

### 6. `runtime/jobs.py` — pass `enrichment_run_id` to queued enrichment jobs

When the queued enrichment job calls `run_turn_enrichment()`, generate and pass
`enrichment_run_id=uuid.uuid4().hex` so queued runs are also idempotent.

### 7. Tests

Two fixtures:
- **Idempotency:** Call `run_turn_enrichment()` twice with the same `enrichment_run_id`
  on the same bead. Assert that association count and claim update count are identical
  after both calls (no duplicates), and that the second call returns `idempotent: True`.
- **Stage 4 atomicity:** Verify that `crawler_merge` and log clear happen within the
  same lock acquisition by mocking `store_lock` and confirming both writes occur inside
  one context manager entry.

---

## Dependencies / risks

- `_grounding_hash()` must accept a plain string, not just a list of bead IDs. Confirm
  the existing implementation or add an overload.
- If `enrichment_run_id` is not threaded through the queued job path, the idempotency
  gate only protects inline calls. Confirm that `jobs.py` passes it before closing.
- The `.beads/events/` directory is shared with other event files. The naming convention
  `enrichment-{bead_id}-{run_id[:8]}.jsonl` must not collide with existing event file
  names. Audit existing file names before adding.
