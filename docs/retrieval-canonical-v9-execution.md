# Retrieval Canonical v9 — Execution Checklist

Branch baseline: `feat/retrieval-canonical-v9-slice1`

## Slice 1 (this branch): foundation + lifecycle wiring

### A. Shared normalization (single source of truth)
- [ ] Add `core_memory/retrieval/normalize.py`
- [ ] Export canonical helpers for tokenize/stem/stopword + intent classify
- [ ] Keep existing `query_norm.py` as wrapper shims for compatibility in this slice

### B. Visible corpus builder
- [ ] Add `core_memory/retrieval/visible_corpus.py`
- [ ] Merge session surface + index projection by bead id
- [ ] Admission contract:
  - include `open|candidate|promoted|archived`
  - exclude `superseded`
  - exclude `retrieval_eligible == false`
  - exclude system/checkpoint rows by default
- [ ] Ensure canonical row fields are present (`bead_id,status,source_surface,session_id,created_at,incident_id,tags,semantic_text,lexical_text,source_turn_ids`)

### C. Lifecycle manager (dirty + checkpoints)
- [ ] Add `core_memory/retrieval/lifecycle.py`
- [ ] Add `mark_semantic_dirty(...)`, `mark_trace_dirty(...)`
- [ ] Add `mark_turn_checkpoint(...)`, `mark_flush_checkpoint(...)`
- [ ] Add `enqueue_semantic_rebuild(...)` (coalescing queue semantics)

### D. Mutation wiring (authoritative dirty marks)
- [ ] Wire semantic dirty on `add_bead(...)`
- [ ] Wire semantic dirty on `promote(...)`, `compact(...)`, `uncompact(...)`
- [ ] Wire trace dirty on `link(...)`
- [ ] Keep `recall(...)` as explicit non-trigger

### E. Runtime observability checkpoints
- [ ] Mark turn checkpoint in `process_turn_finalized(...)`
- [ ] Mark flush checkpoint in `process_flush(...)`

### F. Tests (slice-1 only)
- [ ] visible-corpus admission + merge behavior
- [ ] add_bead marks semantic dirty
- [ ] recall does not mark semantic dirty
- [ ] link marks trace dirty

## Slice 2 (next branch)
- semantic backend manifest/rows/lock/queue + stale serving

## Slice 3
- canonical planner (`search`, `trace`, `execute`) + lexical rescue rules

## Slice 4
- surface supersession (HTTP/CLI/wrappers/evals) + deprecation shims

