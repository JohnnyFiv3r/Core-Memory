# LoCoMo smoke test

One conversation of the [LoCoMo](https://github.com/snap-research/locomo)
benchmark, run through the engine with live models: Claude Haiku 5.5 reads
each chunk, and Claude Opus 5.5 judges how each new bead relates to the beads
near it in time. It shows that the engine works end to end against a real
provider, what it stores, and what that costs. It does not answer LoCoMo's
questions and gives no score.

**It spends real money.** The budgets in `core-memory.toml` hold one UTC
day's worst case under $25: about $1 for the reader and $23 for the judge, if
every call used all the output it may. Work the budget does not cover is
left in the queue, and the report says so.

## What it needs

- `core-memory`, built from this tree (`cargo build --release -p
  core-memory-sidecar`), on `PATH` or named by `CORE_MEMORY_BIN`;
- an empty PostgreSQL 15 or newer database the test may fill, in
  `CORE_MEMORY_DATABASE_URL`. A server that offers TLS must have a
  certificate the engine trusts, even on this machine: for a local server
  with the self-signed certificate Ubuntu gives it, add `?sslmode=disable`
  to the URL;
- an Anthropic API key in `CORE_MEMORY_ANTHROPIC_KEY`. The usual
  `ANTHROPIC_API_KEY` is not read, so that other tools on the machine do not
  start using the key;
- `python3`, `curl` and `sha256sum`;
- behind a proxy or gateway that re-signs TLS, its certificate authority in
  `MODELS_CA_FILE`, which the runner writes to the config as `[models]
  ca_file`. Without it nothing is sent: the run stops at once and says to
  set it.

## Running it

```bash
eval/locomo-smoke/run.sh /some/output/folder
```

It downloads LoCoMo and checks the file against the checksum it was written
for, turns the conversation `conv-26` (419 turns over 19 sessions) into
SourceEvents, admits them, and runs `core-memory read run` until nothing is
owed, nothing has been due for ten minutes, or two hours have passed. Then it
exports what was stored and writes `report.md`: counts, the calls and their
cost from the token counts the API reported, the work left, and samples of
what the judge said, with the text of both beads, for a person to read.

The output folder holds the conversation's text (`events.jsonl`,
`export.jsonl`). Keep it out of this repository.

The reader types chunks with memoriesQL's current bead types at its schema
60: the keys, revisions, definitions and order its `engine_vocabulary_v1()`
returns, written into `core-memory.toml`, since the test runs without
memoriesQL.

Settings: `LOCOMO_SAMPLE` for another conversation, `MAX_MINUTES` for another
time limit, and `JUDGE_MODEL`, `JUDGE_EFFORT` and `READER_EFFORT` to run the
same conversation with other models, and `READER_ONLY=1` to read it and judge nothing (see `configure.py`, and
[`eval/compare`](../compare/) to compare two runs). The test refuses to start with less than the time limit and half
an hour left in the UTC day, since a run past midnight would get a second
day's budget.

## How a conversation becomes events

Each turn is one `core.chat_transcript` event in scope `locomo`, workspace
`smoke`, with its dialogue ID (`D3:7`) as its external ID and the speaker as
its actor. A session gives only its start time, to the minute, so its turns
are placed one second apart from that time, in order. Captions of shared
images are not part of what was said and are left out.
