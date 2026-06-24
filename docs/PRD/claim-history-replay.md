# PRD: Claim-Slot `history()` Replay Verb

**Status:** Spec only — no implementation exists
**Effort:** ~0.5 day spec review + ~1.5 days implementation
**Depends on:** Temporal Truth Contract (TT-1), claim layer (shipped)
**Related prior art:** kaeru `history()` verb (LamantinAI) — same idea, narrower scope here

---

## Problem

Core Memory already stores everything needed to answer *"how did this fact
change over time?"* — but it has no read verb that returns it.

The bi-temporal substrate is in place: every `subject:slot` claim chain carries
`effective_from`/`effective_to` (valid time), `recorded_at` (transaction time),
an ordered `claim_updates` chain (`chain_seq`, `supersede`/`retract`/`conflict`
decisions), and a `CONTRADICTS` edge type. The resolver already answers a
**point-in-time** question — `resolve_all_current_state(root, as_of=T)` returns
the state that was valid at `T` — and `build_claim_timeline()` already returns the
ordered **event stream** for a slot (`assert` / `supersede` / `retract` / …),
filtered by `as_of`.

What is missing is the verb that ties those two together: a **resolved-state
series**. There is no way to ask "replay this slot and tell me what the *answer*
was at each breakpoint, and exactly when it changed." Callers can get the events
(`build_claim_timeline`) or the answer-at-one-time (`resolve_all_current_state`),
but not the answer-over-time. That projection is the deliverable.

This matters because the conflict story is currently only legible as a
point-in-time snapshot. Recall attaches `claim_slots` and `conflicts` to a
`RecallResult` for *now* (or for one `as_of`), but cannot show *when* a
`CONTRADICTS` opened, how long it stood, and which update resolved it via
`SUPERSEDES`. That sequence is exactly the signal the dreamer and answer policy
already consume — we just never expose it.

---

## User value

- An agent (or a human via MCP/HTTP) can ask "what has Evan's role been over
  time?" and get an ordered series: `analyst (2024-01 → 2025-03) → manager
  (2025-03 → open)`, each step grounded in the claim/update that caused the
  transition.
- Conflicts become a timeline, not a flag: "these two claims contradicted from
  2025-03 to 2025-04, resolved by supersession at 2025-04-02."
- Auditability — every state transition is traceable to the `claim_id` /
  `update_id` and the `source_bead_id` / `source_turn_ids` that drove it. This is
  the provenance answer-policy already needs for `answer_historical`.
- A foundation for "show your work" recall: `memory_trace` can cite *when* truth
  changed instead of only the current resolved value.

---

## Current state

| Component | Status |
|-----------|--------|
| Bi-temporal fields (`effective_from`/`effective_to`, `recorded_at`) | ✅ Shipped (`schema/models.py`, TT-1) |
| `as_of` point resolution (`resolve_all_current_state(..., as_of=)`) | ✅ Shipped (`claim/resolver.py:17`) |
| Event timeline (`build_claim_timeline(claims, updates, as_of=)`) | ✅ Shipped (`claim/resolver_helpers.py:39`) — **events only, no state projection** |
| Per-point visibility predicates (`claim_visible_as_of`, `update_visible_as_of`) | ✅ Shipped (`temporal/resolution.py`) |
| `CONTRADICTS` edge / `CONFLICT` claim decision | ✅ Shipped (`schema/models.py:149,191`) |
| **Resolved-state series (replay) function** | ❌ **Missing** |
| **Read-surface verb (MCP/HTTP/recall) exposing the series** | ❌ **Missing** |
| `ClaimHistoryItem` / series contract type | ❌ **Missing** |

**Key distinction:** `build_claim_timeline` answers *"what events happened?"*.
This PRD adds *"what was the resolved answer between each pair of events, and when
did it change?"* — the projection over the timeline, not the timeline itself.

---

## Success criteria

1. A new function `replay_claim_slot(root, subject, slot, *, as_of_max=None,
   session_id=None)` returns an ordered list of resolved-state segments for the
   slot, each segment carrying `valid_from`, `valid_to` (exclusive, `None` =
   open), `value`, `status` (`active` / `superseded` / `retracted` / `conflict`),
   `claim_id`, and `caused_by` (the update/claim that opened the segment).
2. Each segment's resolved `value` and `status` is **identical** to what
   `resolve_all_current_state(root, as_of=segment.valid_from)` returns for that
   slot at the segment's start. (Replay composes the existing resolver — it does
   not re-implement resolution.) This is the determinism contract.
3. Conflict windows are represented: when ≥2 claims are simultaneously valid and
   unresolved, the segment `status` is `conflict` and `metadata.conflicting_ids`
   lists the competing `claim_id`s.
4. The series is exposed on the typed MCP read surface as
   `query_claim_history(subject, slot, as_of_max=None)` alongside the existing
   `query_current_state` / `query_temporal_window` / `query_contradictions`
   (`integrations/mcp/typed_read.py`).
5. Breakpoints are derived deterministically from the union of every distinct
   `effective_from`, `effective_to`, and update `effective_from` in the chain —
   timezone-normalized to UTC per TT-1. Two calls over unchanged data return
   byte-identical series.
6. Missing/invalid temporal fields degrade with warnings, never crash (TT-1
   determinism rule). A slot with claims but no temporal bounds returns a single
   open segment `[None, None)`.
7. Empty/unknown slot returns `{"ok": true, "segments": [], "warnings": [...]}` —
   not an error.

---

## Scope

**In:**
- `replay_claim_slot()` in `claim/resolver.py` (read-only; composes
  `read_all_claim_rows`, `build_claim_timeline`, and `resolve_all_current_state`).
- `ClaimHistoryItem` (one segment) + `ClaimHistorySeries` contract types in
  `retrieval/contracts.py`, next to `ClaimSlotItem` / `ConflictItem`.
- `query_claim_history()` typed read in `integrations/mcp/typed_read.py` and its
  registration in the MCP server (`integrations/mcp/protocol_server.py` /
  `registry.py`) and HTTP surface (`integrations/http/server.py`), mirroring how
  `query_temporal_window` is wired.
- Tests: replay-equals-pointwise-resolution, conflict-window representation,
  open-ended segment, no-temporal-bounds fallback, empty slot.

**Out:**
- Generic bead-level timeline replay. This verb is **claim-slot scoped only** —
  it leverages the claim chain, which is already the bi-temporal series. A
  whole-graph "what did memory look like at T" replay is a separate, larger
  effort and explicitly deferred.
- Any change to write-side semantics, claim resolution rules, or the conflict
  model. This is a pure read projection over existing data.
- New persistence, migration, or schema fields. Replay reads what is already
  stored.
- Caching/materialization of series. v1 computes on read; revisit only if a
  benchmark shows it is hot.
- LLM involvement. Replay is deterministic; no agent judgment is invoked (the
  judgment already happened when the claims/updates were written).

---

## Design

### Core function

```python
def replay_claim_slot(
    root: str,
    subject: str,
    slot: str,
    *,
    as_of_max: str | None = None,   # cap the replay window (transaction/valid horizon)
    session_id: str | None = None,
) -> dict:
    """
    Replay a subject:slot claim chain as an ordered resolved-state series.

    Returns:
      {
        "ok": True,
        "subject": subject,
        "slot": slot,
        "segments": [ClaimHistoryItem.to_dict(), ...],  # chronological, non-overlapping
        "warnings": [...],
      }

    Each segment is the resolved state that held between two adjacent
    breakpoints. Resolution at each breakpoint delegates to the existing
    as_of resolver — replay only chooses the breakpoints and stitches segments.
    """
```

### Algorithm (composition, not new resolution logic)

1. `claims, updates = read_all_claim_rows(root)`; filter to this `subject:slot`.
2. **Breakpoints** = sorted, de-duplicated UTC set of every `effective_from`
   (claims + updates) and every `effective_to` in the chain, dropping any
   `> as_of_max`. Prepend the earliest claim start.
3. For each breakpoint `t`, call `resolve_all_current_state(root, as_of=t)` and
   read the `subject:slot` slot from its result. (Reuses the shipped resolver —
   satisfies success criterion #2 by construction.)
4. **Coalesce** adjacent breakpoints whose `(value, status, claim_id)` are equal
   into one segment `[valid_from, valid_to)`; `valid_to` is the next *changing*
   breakpoint, or `None` if the last segment is open.
5. Tag each segment's `caused_by` with the update/claim whose `effective_from`
   equals `valid_from` (from `build_claim_timeline`'s event for that point), and
   for `conflict` segments populate `metadata.conflicting_ids`.
6. Collect warnings (unparseable timestamps, claims with no bound) per TT-1.

This is intentionally O(breakpoints × resolve-cost). Claim chains per slot are
short; if a pathological slot is found, the resolver call can be replaced by an
incremental fold in a follow-up without changing the contract.

### Contract type (`retrieval/contracts.py`)

```python
@dataclass
class ClaimHistoryItem:
    """One resolved-state segment in a claim-slot replay."""
    valid_from: str | None          # ISO8601 UTC; None = open start
    valid_to: str | None            # exclusive; None = still in effect
    value: Any = None
    status: str = ""                # active | superseded | retracted | conflict
    claim_id: str = ""
    caused_by: dict[str, Any] = field(default_factory=dict)  # {kind, id, source_bead_id}
    metadata: dict[str, Any] = field(default_factory=dict)   # conflicting_ids, source_turn_ids

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

### MCP typed read (`integrations/mcp/typed_read.py`)

```python
def query_claim_history(
    *,
    root: str = ".",
    subject: str | None = None,
    slot: str | None = None,
    slot_key: str | None = None,    # accept "subject:slot" like the sibling verbs
    as_of_max: str | None = None,
) -> dict[str, Any]:
    """Return the resolved-state series for a claim slot. Mirrors
    query_temporal_window's request/response shape and error handling."""
```

Wire it into the registry/server the same way `query_temporal_window` is wired;
add the HTTP route alongside the existing temporal endpoints.

---

## Example

Query: `query_claim_history(subject="Evan", slot="role")`

```json
{
  "ok": true,
  "subject": "Evan",
  "slot": "role",
  "segments": [
    {
      "valid_from": "2024-01-10T00:00:00Z",
      "valid_to": "2025-03-02T00:00:00Z",
      "value": "analyst",
      "status": "superseded",
      "claim_id": "clm_a1",
      "caused_by": {"kind": "assert", "id": "clm_a1", "source_bead_id": "bd_11"}
    },
    {
      "valid_from": "2025-03-02T00:00:00Z",
      "valid_to": null,
      "value": "manager",
      "status": "active",
      "claim_id": "clm_a2",
      "caused_by": {"kind": "supersede", "id": "upd_7", "source_bead_id": "bd_42"}
    }
  ],
  "warnings": []
}
```

Conflict example (two valid, unresolved):

```json
{
  "valid_from": "2025-03-02T00:00:00Z",
  "valid_to": "2025-04-02T00:00:00Z",
  "value": "manager",
  "status": "conflict",
  "claim_id": "clm_a2",
  "metadata": {"conflicting_ids": ["clm_a2", "clm_a3"]}
}
```

---

## Implementation tasks

1. **`claim/resolver.py`** — Implement `replay_claim_slot()` per the algorithm
   above. Reuse `read_all_claim_rows`, `build_claim_timeline`, and
   `resolve_all_current_state`; do not duplicate resolution logic.
2. **`retrieval/contracts.py`** — Add `ClaimHistoryItem` (and an optional
   `ClaimHistorySeries` wrapper) next to `ClaimSlotItem` / `ConflictItem`.
3. **`integrations/mcp/typed_read.py`** — Add `query_claim_history()`; accept
   `subject`+`slot` or `slot_key` via the existing `_slot_key()` helper.
4. **`integrations/mcp/protocol_server.py` / `registry.py`** — Register the new
   tool with a JSON schema mirroring `query_temporal_window`.
5. **`integrations/http/server.py`** — Add the HTTP route next to the temporal
   endpoints (read-only GET).
6. **Public surface** — If the typed reads are re-exported, add
   `query_claim_history` to `docs/public_surface.md` and the relevant
   `__init__` exports. Do **not** add a new flat file at `core_memory/` root.
7. **Tests** (`tests/test_claim_history_replay.py`):
   - Replay segment values/status equal pointwise `resolve_all_current_state` at
     each `valid_from` (determinism contract, success criterion #2).
   - Conflict window produces a `conflict` segment with `conflicting_ids`.
   - Open-ended final segment has `valid_to = null`.
   - Slot with claims but no temporal bounds → single `[null, null)` segment.
   - Empty/unknown slot → `{"ok": true, "segments": []}`.
   - Unparseable timestamp → warning emitted, no crash.

---

## Dependencies / risks

- **Resolver coupling:** Correctness rests on `resolve_all_current_state` being
  the single source of truth for as_of resolution. Replay must call it, not
  re-derive — otherwise the two can drift and success criterion #2 breaks. This
  is also the upside: any future fix to resolution rules is inherited for free.
- **Breakpoint completeness:** If a transition is encoded only via an update with
  no `effective_from` (relying on `recorded_at`), the breakpoint set must fall
  back to the same anchor order the resolver uses (`effective_from` → `observed_at`
  → `recorded_at` → `created_at`, per `temporal/resolution.py`). Reuse those
  helpers rather than re-listing the precedence.
- **Whole-second temporal resolution:** Two updates within the same second can
  collapse to one breakpoint. Acceptable for v1 (matches store granularity);
  document it. Order ties by `chain_seq` for stability.
- **Cost:** O(breakpoints) resolver calls. Fine for normal chains; flagged above
  with a clean upgrade path (incremental fold) that preserves the contract if a
  hot slot ever appears.
- **Layering:** `replay_claim_slot` lives in domain logic (`claim/`), the verb in
  `integrations/` — dependencies flow downward, no upward imports. The verb
  consumes the public API path like every other typed read.
