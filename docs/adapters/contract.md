# Adapter lifecycle contract

Status: v1 locked contract for adapter authors.

Core Memory adapters translate host-runtime lifecycle events into three canonical hooks. The hooks are not new APIs; they are the existing runtime entrypoints, documented here as the public adapter contract.

| Public hook | Runtime function | Cardinality | Schedulable? |
|---|---|---:|---|
| `on_session_start` | `process_session_start` | once per session | no |
| `on_turn_end` | `process_turn_finalized` | once per completed turn | no |
| `on_session_end` | `process_flush` | one or more per session | yes |

`on_session_end` intentionally maps to `process_flush`. It may fire at actual session end, on rolling-window thresholds, on idle timers, or from scheduled maintenance. The name does not mean the session is terminating.

## Hook surfaces

Fields are documented in three tiers:

- **Required:** enough to build a correct adapter.
- **Optional context:** populate when the host runtime exposes it.
- **Advanced internals:** available for transparency; most adapters should leave defaults alone.

### `on_session_start` → `process_session_start`

| Tier | Field | Type | Notes |
|---|---|---|---|
| Required | `root` | `str` | Path to the Core Memory store. |
| Required | `session_id` | `str` | Unique-within-store session identifier. Generate one if the host runtime has no session id. |
| Optional context | `source` | `str` | Adapter/runtime source. Default is `"runtime"`; use values such as `"openclaw"`, `"langchain"`, or `"manual"`. |
| Advanced internals | `max_items` | `int` | Rolling-window injection size. Default is `80`; override only with measured cause. |

### `on_turn_end` → `process_turn_finalized`

| Tier | Field | Type | Notes |
|---|---|---|---|
| Required | `root` | `str` | Path to the Core Memory store. |
| Required | `session_id` | `str` | Must match the session opened through `on_session_start`. |
| Required | `turn_id` | `str` | Unique within the session. Reuse is undefined behavior. |
| Required | `turns` | `list[Turn]` | Canonical multi-speaker shape: each item has `speaker`, `role`, and `content`. |
| Optional authorship | `crawler_updates` | `AgentAuthoredUpdatesV1 | None` | Typed top-level `agent_authored_updates.v1` payload. The primary agent authors it in `inline` mode. `metadata.crawler_updates` is a deprecated one-release alias; top-level wins and records a warning. |
| Optional authorship | `authoring_mode` | `inline | delegated | None` | Use `inline` when the live primary agent supplies `crawler_updates`. A passive adapter may explicitly request `delegated`; Core Memory never silently invokes a model. |
| Optional context | `metadata` | `dict[str, Any]` | Free-form per-turn context. Reserved/high-value keys include `retrieved_beads`, `used_memory`, `correction_triggered`, and `reflection_triggered`. |
| Optional context | `tools_trace` | `list[dict]` | Tool calls made during the turn. Populate when the host exposes tool events. |
| Optional context | `reasoning_trace` | `list[dict]` | Post-PRD #10 field for reasoning/thinking blocks. Not yet a runtime parameter. |
| Optional context | `origin` | `str` | What triggered this write. Default is `"USER_TURN"`. |
| Optional context | `transaction_id` | `str | None` | Caller-side transaction id. |
| Optional context | `trace_id` | `str | None` | Distributed tracing span/request id. |
| Advanced internals | `trace_depth` | `int` | Recursion safety counter. Adapters should leave default `0`. |
| Advanced internals | `mesh_trace` | `list[dict] | None` | Mesh coordination trace. |
| Advanced internals | `window_turn_ids` | `list[str] | None` | Manual rolling-window scope override. |
| Advanced internals | `window_bead_ids` | `list[str] | None` | Manual rolling-window scope override. |
| Advanced internals | `policy` | `SidecarPolicy | None` | Per-call sidecar policy override. Defaults from runtime/store configuration. |

### `on_session_end` → `process_flush`

| Tier | Field | Type | Notes |
|---|---|---|---|
| Required | `root` | `str` | Path to the Core Memory store. |
| Required | `session_id` | `str` | Session being flushed/compacted. |
| Required | `promote` | `bool` | Convention: `True`. Promotes eligible candidate beads during flush. |
| Required | `token_budget` | `int` | Convention: `800` for lightweight adapters; adapters may use higher measured defaults. |
| Required | `max_beads` | `int` | Convention: `10` for lightweight adapters; adapters may use higher measured defaults. |
| Optional context | `source` | `str` | Trigger source, e.g. `"session_end"`, `"scheduled"`, or adapter name. |
| Optional context | `flush_tx_id` | `str | None` | Caller-side flush transaction id. |
| Operator override | `semantic_override` | `bool` | Default `false`. Set only to waive a latest pending semantic turn. |
| Operator override | `override_operator` | `str` | Required with `semantic_override=true`; written to append-only waiver audit. |
| Operator override | `override_reason` | `str` | Required with `semantic_override=true`; written to append-only waiver audit. |

The operational kwargs on `process_flush` are required by the function signature. Adapter docs should lead with the conventions above unless an adapter has measured defaults already in production.

## Fire conditions

### `on_session_start`

- Fires once when a new session begins.
- Adapters must call it before the first `on_turn_end` for that session.
- Repeated calls with the same `session_id` are allowed; the runtime treats them as rolling-window re-injection/refresh, not duplicate session creation.

### `on_turn_end`

- Fires after the agent has completed its response for a turn.
- Before-send is preferred when available: if outbound delivery fails, memory still lands.
- After-send is acceptable when the host runtime only exposes post-send hooks.
- `turn_id` values must be unique within the session.
- Adapters should serialize concurrent `on_turn_end` calls for the same session. Runtime serialization is a safety net, not the primary contract.

### `on_session_end`

- Fires whenever compaction should run for a session.
- Multi-fire is expected: long sessions may flush mid-session and again at actual end.
- Valid triggers include event-driven session end, rolling-window threshold, idle timer, scheduled maintenance, or explicit user/operator request.
- Re-firing on already compacted state is safe; it should be a no-op or incremental flush.
- This is the only schedulable hook. `on_session_start` and `on_turn_end` are strictly event-driven.

## Adapter/agent/runtime responsibility split

| Responsibility | Adapter | Agent | Runtime |
|---|:---:|:---:|:---:|
| Detect lifecycle events in the host system | ✓ | | |
| Map host events to canonical hook calls | ✓ | | |
| Construct `Turn` objects / `{speaker, role, content}` turn dictionaries | ✓ | | |
| Choose explicit `inline` or `delegated` authorship | ✓ | | |
| Populate `metadata`, `tools_trace`, and reasoning trace when available | ✓ | | |
| Author bead meaning, retrieval framing, claims, associations, and promotion reviews | | ✓ | |
| Surface adapter-side errors to the host | ✓ | | |
| Validate typed authored updates and enforce structural guardrails | | | ✓ |
| Persistence: beads, archive records, indexes, side logs | | | ✓ |
| Claims, compaction, promotion locking, and myelination mechanics | | | ✓ |
| Retrieval: search, trace, execute, recall orchestration | | | ✓ |
| Canonical write-boundary error handling | | | ✓ |

Adapters must not reach past the contract into `MemoryStore` or low-level files for performance or convenience. Doing so creates adapter-specific memory semantics and breaks ecosystem parity.

## Processed turn receipt

Processed Python callers should use
`core_memory.integrations.api.write_turn_finalized(...)` (also exported as
`core_memory.write_turn_finalized`). The existing
`core_memory.integrations.api.emit_turn_finalized(...)` remains event-only for
one compatibility window and returns an event id rather than semantic outcome.

Processed Python, HTTP `/v1/memory/turn-finalized`, MCP
`write_turn_finalized`, and `/v1/mcp/write-turn-finalized` return the same
`memory.turn_finalized_receipt.v2` fields:

- `semantic_status`: `committed`, `pending`, `repair_required`, `waived`, or
  `failed`;
- `bead_id`: populated only from canonical current-turn bead lookup;
- independent `authorship` and `validation` evidence;
- `derived.failures`, which never erases an already committed primary bead;
- `associations.status`: exactly `complete`, `pending`, `pending_judge`,
  `failed`, or `skipped`;
- `queue.status` and durable queue item id.

`accepted=true` means the finalized turn was durably accepted. It does not by
itself mean a semantic bead committed. Hosts should gate semantic success on
`semantic_status=committed|waived` and use `retryable` plus the stable error
code for unresolved writes.

Hard typed authorship is the runtime default. A missing payload returns
`semantic_status=pending`; an invalid payload returns
`semantic_status=repair_required`. Both retain the raw turn event and write no
canonical fallback context bead. `warn` remains an explicit compatibility mode.

Delegated repair is separate from ordinary delegated authorship and is disabled
unless `CORE_MEMORY_AGENT_AUTHORED_REPAIR=1` or the caller supplies an explicit
runtime repair policy. Repair uses the same `turn_memory_authoring` contract.
Its receipt sets `authorship.source=repair_agent`, preserves
`primary_authorship` and `repair_authorship`, and provides `repaired_fields`
plus `field_provenance`. A valid primary-agent v1 row never passes through the
narrow bead-judge fallback.

Flush checks the canonical bead for only the latest finalized turn. An older
pending turn does not wedge a later committed turn, but remains visible through
`inspect_state(...).runtime.semantic_writes` and `core-memory doctor`. Pending
age warns after five minutes and is critical after sixty minutes. A latest
pending turn requires an append-only operator waiver before flush can proceed.
An old deterministic stub does not satisfy the barrier when semantic state is
`pending` or `repair_required`.

## Migration note

`user_query` / `assistant_final` are no longer accepted by `process_turn_finalized(...)`. Adapters must pass `turns=[...]`. The runtime derives legacy read-only compatibility fields internally for older downstream policy code.

`crawler_updates` and `authoring_mode` are now part of the turn-envelope hash. An
in-flight retry produced by an older adapter can therefore appear as a changed or
superseded envelope hash after upgrade. Memory-pass identity is unchanged and
remains `(session_id, turn_id)`.
