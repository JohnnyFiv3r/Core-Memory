# Multi-speaker turn schema

Core Memory's canonical write input is now a list of attributed turns:

```python
from core_memory import Turn, process_turn_finalized

process_turn_finalized(
    root="./memory",
    session_id="planning",
    turn_id="t1",
    turns=[
        Turn(speaker="john@example.com", role="user", content="We need a database choice."),
        Turn(speaker="claude", role="assistant", content="Decision: choose Postgres because JSONB lowers integration risk."),
    ],
)
```

Each turn has:

- `speaker` — required stable speaker identity (`john@example.com`, `assistant`, `alice`, etc.)
- `role` — one of `user`, `assistant`, or `other` (defaults to `other` unless inferred by helpers)
- `content` — utterance text
- `ts` — optional timestamp
- `metadata` — optional per-utterance metadata

## Quick capture helper

For the common one-user/one-assistant case, use `capture(...)`:

```python
from core_memory import capture

capture(
    root="./memory",
    session_id="planning",
    turn_id="t1",
    user="We need a database choice.",
    assistant="Decision: choose Postgres because JSONB lowers integration risk.",
)
```

Override speaker identity with `as_user=` and `as_assistant=`:

```python
capture(
    root="./memory",
    session_id="planning",
    turn_id="t1",
    user="Ship the bridge thin.",
    assistant="Noted.",
    as_user="john@example.com",
    as_assistant="openclaw",
)
```

## Hard-cut migration

`user_query=` and `assistant_final=` are no longer accepted by the public finalized-turn write surfaces.

Old:

```python
process_turn_finalized(
    root="./memory",
    session_id="planning",
    turn_id="t1",
    user_query="We need a database choice.",
    assistant_final="Decision: choose Postgres.",
)
```

New:

```python
process_turn_finalized(
    root="./memory",
    session_id="planning",
    turn_id="t1",
    turns=[
        {"speaker": "user", "role": "user", "content": "We need a database choice."},
        {"speaker": "assistant", "role": "assistant", "content": "Decision: choose Postgres."},
    ],
)
```

The runtime still derives compatibility fields (`user_query`, `assistant_final`, and `turn_text`) internally for older downstream policy/retrieval code. Those are derived fields, not public capture inputs.

## Provenance

Captured turn records and crawler envelopes include:

- `turns` — serialized attributed utterances
- `speakers` — ordered unique speaker list
- `turn_text` — readable joined transcript text
- existing `source_turn_ids`
- new `source_turn_ref` where a single canonical source turn reference is needed
