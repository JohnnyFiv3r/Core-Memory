# Reader probe

The second live run of `eval/locomo-smoke` typed every chunk, small talk
included, and abstained on none, although the reader's prompt asks it to
abstain on a message that only keeps a conversation going. Four harmless
questions were also refused. This probe finds out why.

It sends the engine's chunk reader 24 turns of LoCoMo's `conv-26`: 14 of
small talk, where abstaining is right (the four that were refused among
them), and 10 that state something a type describes. It sends them four
times, changing one thing each time:

1. as in the second run: Claude Haiku 5.5 at effort `low`, eight to a call;
2. at effort `medium`;
3. one to a call;
4. with the reply shape not enforced, so the model answers from the prompt
   alone.

The requests are the engine's own: its prompt and reply shape, its Anthropic
request body and its HTTP client. For each call it prints how the call ended
(`stop_reason`, so a refusal and a cut-off reply are told apart), whether the
engine would accept the reply, and what was chosen for each turn. A summary
table ends the output.

**It spends real money, very little:** 4 + 4 + 24 + 4 = 36 calls of Claude
Haiku 5.5, about $0.03 in all.

## Running it

```bash
curl -fsSL -o /tmp/locomo10.json \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  /tmp/locomo10.json" | sha256sum -c -
CORE_MEMORY_ANTHROPIC_KEY=... MODELS_CA_FILE=... \
  cargo run --release -p reader-probe -- /tmp/locomo10.json
```

`MODELS_CA_FILE` is needed only behind a proxy that re-signs TLS. With
`--dry-run` it prints the first request of each variant and sends nothing,
and needs no key.
