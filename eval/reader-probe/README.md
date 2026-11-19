# Reader probe

Sends the engine's chunk reader the same 30 turns of LoCoMo's `conv-26`
under three vocabularies, and prints, for each turn, the type the model
chose and what it quoted: the entities with their kinds, the actions and
the outcomes. A table at the end puts the three side by side.

The requests are the engine's own: its reader prompt and reply shape
(`read::render`), its Anthropic request body and its HTTP client, with the
reader's settings of `eval/locomo-smoke` (Claude Haiku 5.5 at effort `low`,
eight turns to a call). Only the vocabulary changes:

1. memoriesQL's 12 bead types, as its registry defines them;
2. the 12 and `social`: a greeting, thanks, compliment, sympathy or check-in
   that tends a relationship rather than telling news;
3. 11 and `social`, with one `goal` in place of `goal_declaration` and
   `goal_update`: how an update relates to its goal is the judge's to say.

The turns are 14 of small talk, 10 that tell something about the speaker,
and 6 about Caroline's goals, stated and then restated with their progress.

**It spends real money, very little:** 12 calls of Claude Haiku 5.5, about
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
