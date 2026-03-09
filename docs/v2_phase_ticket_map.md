# V2 Phase Ticket Map (P3/P4/P5)

Status: Canonical execution map
Source: `docs/v2_gap_checklist.md`

## V2-P3 — Transactionalization + authority hardening

### V2-P3-T1
- Title: Canonical runtime center module definition
- Goal: consolidate orchestration ownership and reduce split entrypoint ambiguity
- Risk: Medium

### V2-P3-T2
- Title: Session authority cutover (live session surface)
- Goal: session file is authoritative during active session; index becomes projection/support
- Risk: High

### V2-P3-T3
- Title: Enrichment barrier enforcement before flush
- Goal: block flush progression until final-turn enrichment checkpoint is complete
- Risk: High

### V2-P3-T4
- Title: Trigger replay/idempotency hardening
- Goal: deterministic duplicate-trigger immunity across turn + flush paths
- Risk: Medium

### V2-P3-T5
- Title: Flush failure injection + resume semantics
- Goal: stage-level crash/retry resilience with deterministic resume
- Risk: High

## V2-P4 — Surface and schema embodiment closure

### V2-P4-T1
- Title: Rolling window first-class surface contract
- Goal: make rolling projection/store semantics explicit and testable
- Risk: Medium

### V2-P4-T2
- Title: Rolling FIFO determinism under budget pressure
- Goal: strict recency/token behavior validated with fixtures
- Risk: Medium

### V2-P4-T3
- Title: Association subsystem extraction
- Goal: explicit `association/` package for causal/promotion shaping passes
- Risk: High

### V2-P4-T4
- Title: Schema enforcement reconciliation
- Goal: align `models.py` and runtime enums to canonical `schema.py`
- Risk: Medium

### V2-P4-T5
- Title: Association bead-type decision closure
- Goal: ADR + implementation for bead vs edge taxonomy decision
- Risk: Medium

## V2-P5 — Legacy cleanup + integration posture

### V2-P5-T1
- Title: SpringAI bridge framing cleanup
- Goal: align integration posture/modules/docs to SpringAI bridge intent
- Risk: Low-Medium

### V2-P5-T2
- Title: Legacy extract/wrapper deprecation pass
- Goal: mark and reduce non-canonical paths while preserving operator fail-safe needs
- Risk: Medium

### V2-P5-T3
- Title: Final deprecation inventory closeout
- Goal: resolve or justify all active legacy entries
- Risk: Low-Medium
