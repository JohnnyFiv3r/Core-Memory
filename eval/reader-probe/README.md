# Reader probe

Sends the engine's chunk reader the same 30 turns of LoCoMo's `conv-26`
under two vocabularies, and prints, for each turn, the type the model chose
and what it quoted: the entities with their kinds, the actions and the
outcomes. A table at the end puts the two side by side.

The requests are the engine's own: its reader prompt and reply shape
(`read::render`), its Anthropic request body and its HTTP client, with the
reader's settings of `eval/locomo-smoke` (Claude Haiku 5.5 at effort `low`,
eight turns to a call). Only the vocabulary changes:

1. memoriesQL's bead types with `goal_declaration` and `goal_update` merged
   into one `goal`: 11 types;
2. the 11 and `social`: a greeting, thanks, compliment, sympathy or
   check-in that tends a relationship and states nothing else; a message
   that also tells something takes the type of what it tells.

The turns are 14 of small talk, 10 that tell something about the speaker,
and 6 about Caroline's goals, stated and then restated with their progress.
The vocabularies of the earlier run are kept in `earlier_variants`.

**It spends real money, very little:** 8 calls of Claude Haiku 5.5, under
$0.01. It stops at once if the key is turned down.

## Running it

```bash
curl -fsSL -o /tmp/locomo10.json \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  /tmp/locomo10.json" | sha256sum -c -
CORE_MEMORY_ANTHROPIC_KEY=... MODELS_CA_FILE=... \
  cargo run --release -p reader-probe -- /tmp/locomo10.json
```

`MODELS_CA_FILE` is needed only behind a proxy that re-signs TLS. With
`--dry-run` it prints the first request of each vocabulary and sends
nothing, and needs no key.

## What it has found

**2026-11-19, vocabularies** ($0.009, one run of 30 turns, so a turn can
flip between runs):

| Vocabulary | Small talk (14) | Statements (10) | Goal turns (6) |
|---|---|---|---|
| memoriesQL's 12 | 14 untyped | all typed but D3:16 | 5 `goal_declaration`, 1 `goal_update` |
| 12 and `social` | 9 `social`, 2 untyped, 3 in an invalid reply | D3:16 `social`, 4 in an invalid reply | 5 `goal_declaration`, 1 in an invalid reply |
| 11 and `social`, one `goal` | 14 `social` | 4 `social` | 4 `goal`, 1 `action`, 1 `preference` |

- With the reply shape not enforced the reader quotes again: 17 to 20
  entities, 12 to 14 actions and 2 to 4 outcomes in 30 turns, using the new
  kinds (`group`, `event`, `project`, `organization`).
- Without a rule of its own about small talk, the reader left all 14 turns
  untyped when no type fit them, and typed them `social` when that was
  offered.
- `social` as defined here takes statements that open with thanks ("Thanks,
  Melanie! This necklace is super special to me - a gift from my grandma"):
  its definition needs to say that a message which also tells something
  takes the type of what it tells.
- `goal_declaration` and `goal_update` were not told apart reliably: one of
  three updates was typed `goal_update`. One `goal` took two of the three
  declarations and two of the three updates; the others went to
  `preference` and `action`. How an update relates to its goal is
  what the judge's edges record (a `reaffirm` chain in both live runs).
- One reply in twelve wrote an outcome as a string; the engine would repair
  it once, which the probe does not.

**2026-11-19, the reply shape.** An earlier version sent 24 turns four ways.
Held to its reply shape by the provider, the model typed all 14 turns of
small talk and wrote about 160 output tokens for eight turns, too few for
any quotes; effort and batch size changed nothing. With the shape not
enforced, it abstained where its prompt said to and typed the rest, writing
several times as much, and the engine accepted every reply. The second live
run's four "refusals" were replies cut off at `max_output_tokens`. Since
reader prompt revision 3 the engine no longer asks the provider to hold the
reader's reply to its shape, and a cut-off reply is reported as
`did_not_fit`. It cost $0.016.
