# Memory Sidecar Integration Spec (OpenClaw Coordinator)

Status: Draft for review (rev 2)
Owner: Core Memory
Goal: Make per-turn memory feel native to OpenClaw (not prompt-dependent)

---

## 1) Product Invariant

**Exactly one memory pass per top-level user message turn**, emitted at coordinator finalize/commit after the final user-visible response is assembled.

### Identity model (explicit)
- `turn_id`: deterministic ID for the user message (stable across retries)
- `transaction_id`: coordinator execution instance (retry/resume/debug)
- `memory_pass_key = session_id + ":" + turn_id` (idempotency key)

Trigger scope:
- top-level coordinator only (`trace_depth == 0`)

Trigger point:
- `on_response_finalized` / commit boundary

---

## 2) Integration Mode

Recommended: **Mode B (side-effect)**

- Main response returns immediately after finalize.
- Memory pass runs async as a side-effect.
- Idempotency ensures safe retries.

Hybrid guardrail (defined):
- On next user turn intake, if pending deltas exist for the session:
  - attempt apply with `max_flush_ms` budget (default `50ms`)
  - if budget exceeded, continue async (no user-visible block)
- Track `delta_apply_lag_turns` metric.

---

## 3) Coordinator Hook Points

Required hook:
- `Coordinator.on_response_finalized(turn_ctx, final_response)`

At this point, coordinator has:
1. completed fanout/mesh calls
2. received sub-results
3. assembled final text/media

Emit `MemoryEvent` here.

Streaming rule:
- emit one event at finalization only (not first token).

---

## 4) Data Contracts

## TurnEnvelope (immutable)

```json
{
  "schema": "openclaw.memory.turn_envelope.v1",
  "session_id": "string",
  "turn_id": "string",
  "transaction_id": "string",
  "trace_id": "string",
  "origin": "USER_TURN|MEMORY_PASS|SYSTEM|SUBAGENT",
  "ts": "ISO8601",
  "ts_ms": 0,
  "user_query": "string",
  "assistant_final_hash": "sha256-hex",
  "assistant_final": "string|null",
  "assistant_final_ref": "string|null",
  "envelope_hash": "sha256-hex",
  "tools_trace": [
    {
      "tool_call_id": "string",
      "name": "string",
      "args_hash": "string",
      "result_hash": "string",
      "category": "retrieval|write|external|internal",
      "ok": true,
      "latency_ms": 0,
      "error": "string|null",
      "redaction_applied": false
    }
  ],
  "mesh_trace": [
    {
      "agent": "string",
      "span_id": "string",
      "parent_span_id": "string|null",
      "capability": "string",
      "input_hash": "string",
      "output_hash": "string",
      "ok": true,
      "latency_ms": 0,
      "error": "string|null"
    }
  ],
  "window_turn_ids": ["string"],
  "window_bead_ids": ["string"],
  "metadata": {
    "channel": "string",
    "chat_type": "direct|group|thread",
    "streamed": true,
    "model": "string",
    "temperature": 0.0,
    "policy_mode": "strict|hybrid",
    "locale": "string"
  }
}
```

Notes:
- Default privacy posture: `assistant_final_hash` + `assistant_final_ref`; full text optional by policy.
- Always include hashes for deterministic replay/debug.

## MemoryEvent

```json
{
  "schema": "openclaw.memory.event.v1",
  "event_id": "string",
  "session_id": "string",
  "turn_id": "string",
  "transaction_id": "string",
  "trace_id": "string",
  "ts": "ISO8601",
  "ts_ms": 0,
  "kind": "TURN_FINALIZED",
  "envelope_ref": "pointer-or-inline"
}
```

## MemoryDelta

```json
{
  "schema": "openclaw.memory.delta.v1",
  "session_id": "string",
  "turn_id": "string",
  "created": [{"bead_id":"string","type":"string","title":"string","body_ref":"string|hash","score":0.0,"reason":"string"}],
  "promoted": [{"bead_id":"string","score":0.0,"reason":"string"}],
  "promotion_candidates": [{"bead_id":"string","score":0.0,"reason":"string"}],
  "suppressed": [{"candidate":"string","reason":"string"}],
  "metrics": {"runtime_ms":0,"created_count":0,"promoted_count":0}
}
```

---

## 5) Idempotency + Mutation Handling

Idempotency key:
- `memory_pass_key = session_id + ":" + turn_id`

Rules:
- If pass for key already `done`, retry is no-op.
- If same key arrives with different `assistant_final_hash`, log `turn_mutation` and run optional amend mode.
- Persist pass status: `pending|running|done|failed`.

Ordering:
- Process by `(ts_ms, turn_id)` where available.
- tolerate out-of-order via idempotency.

---

## 6) Recursion Guard

Memory pass must never re-trigger itself.

- `origin=MEMORY_PASS` => coordinator skips event emission.
- memory worker runs `no_mesh=true` where possible.
- if mesh used in memory worker, disable event emission for those spans.

---

## 7) Budget / Promotion Policy (v1)

Per top-level turn:
- max created beads: `1` (0 allowed)
- max promotions: `1`
- max merge/suppress actions: `1`

Type-aware cooldown:
- maintain per-type promotion budget to avoid starvation
- severity override (safety/compliance/incident) may bypass cooldown within hard cap

Creation threshold:
- score >= 0.75 OR explicit user remember intent

Promotion threshold:
- score >= 0.85 and at least one gate:
  - repeat pattern
  - confirmed outcome
  - explicit user emphasis

---

## 8) Failure Handling

If memory pass fails:
- do not fail user-visible response
- retry with bounded backoff
- emit failure metric/event

No-write fallback (required):
- after retries exhausted, emit fallback delta with suppressed candidates + metrics
- persist envelope for reprocessing

---

## 9) Event Transport

Dual-mode abstraction:
- Dev/edge: append-only JSONL + poller
- Prod: internal queue (optional JSONL mirror for audit)

`envelope_ref` must support inline and pointer forms.

---

## 10) Acceptance Criteria

1. For 100 top-level user turns, exactly 100 memory pass attempts emitted.
2. >=99% complete (`done`) without duplicates.
3. No memory pass triggered from sub-agent/internal mesh hops.
4. Streaming produces exactly one final memory event per turn.
5. Retry does not duplicate bead creation (idempotency proven).
6. Budget compliance: across 100 turns, created <=100 and promotions <=100; violations logged.
7. Shadow mode reports `would_have_created` / `would_have_promoted` for threshold tuning.

---

## 11) Rollout Plan

Phase A (shadow)
- emit envelopes/events only, no writes.
- measure would-have actions + lag metrics.

Phase B (write-on)
- enable create/promote with strict budgets + idempotency.

Phase C (tighten)
- enable lag alerts, optional pre-next-turn flush budget.

---

## 12) Reference Coordinator Wiring (minimal)

Use this at coordinator finalize/commit:

```python
from core_memory.openclaw_integration import coordinator_finalize_hook, process_pending_memory_events

# 1) after final user-visible response is assembled
coordinator_finalize_hook(
    root=CORE_MEMORY_ROOT,
    session_id=session_id,
    turn_id=turn_id,                 # stable user-turn id
    transaction_id=transaction_id,   # execution instance id
    trace_id=trace_id,
    user_query=user_query,
    assistant_final=assistant_final,
    trace_depth=trace_depth,
    origin=origin,
    tools_trace=tools_trace,
    mesh_trace=mesh_trace,
    window_turn_ids=window_turn_ids,
    window_bead_ids=window_bead_ids,
    metadata=metadata,
)

# 2) async sidecar worker tick (or immediate post-commit in dev)
process_pending_memory_events(CORE_MEMORY_ROOT, max_events=50)
```

Rules:
- call finalize once per top-level turn
- do not emit when `origin == MEMORY_PASS`
- keep `turn_id` stable across retries

## 13) Open Questions (for sign-off)

1. Privacy default: full assistant text off (hash+ref only) — agree?
2. Flush budget default (`50ms`) acceptable for chat UX?
3. Severity taxonomy and overrides to adopt in v1?
4. Batch-turn policy: if multiple user messages are coalesced, use synthetic `turn_id` with message array?
