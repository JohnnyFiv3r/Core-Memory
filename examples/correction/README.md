# A correction, end to end

Five chat messages about a launch and a demo. One corrects an earlier one,
and one only half contradicts another. A reader types each message and a
judge relates them. Then recall shows what holds now.

| | Sent | Message |
|---|---|---|
| 1 | Nov 16, 15:00 | Launch plan: we ship the new billing page on Friday, November 20. |
| 2 | Nov 16, 15:04 | Great. I'll have the announcement email ready to go out the morning of the launch. |
| 3 | Nov 17, 10:12 | Change of plan: the launch moves to Monday, November 23. The payments vendor needs two more days to certify the integration. |
| 4 | Nov 17, 11:00 | The launch demo for the sales team is at 3pm on Thursday. |
| 5 | Nov 17, 16:30 | I heard the sales demo might move to 4pm, but I'm not sure that's confirmed. |

## Run it

It calls Anthropic's API, Claude Haiku 5.5 to read and Claude Opus 5.5 to
judge, and costs a few cents. Use an empty database:

```bash
export CORE_MEMORY_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres
export CORE_MEMORY_ANTHROPIC_KEY=...        # your key; never put it in the file

cd examples/correction
core-memory migrate
core-memory scope add pilot --workspace pilot
core-memory scope allow pilot --binding reader   # the reader and the judge share a destination
core-memory ingest --input events.jsonl
core-memory read run      # reads all five
# A judge that reaches a message before the reader has puts it off for up
# to five minutes ([judge] max_wait_for_reads_ms). If `read status` still
# shows judge work ready, run this again after that.
core-memory read run
core-memory recall billing page
core-memory recall launch --include-ended
core-memory recall sales demo
```

## What it gave

Run on 2026-11-21 with `core-memory` 2.0.1:

- **The correction replaces the plan.** The judge said message 3 replaces
  message 1 (`repl`). The engine held that arrow until a second read, made
  without the first answer, gave it again. Only then was it written.
- **Recall answers with what holds now.** `recall billing page` matches
  only message 1, so it answers with message 3, marked as its successor.
  `recall launch --include-ended` returns message 1 as well, with
  `current: false`.
- **A hedge is not a replacement.** The judge read message 5 as
  contradicting message 4 (`!contradicts`), not replacing it. Both stay
  current, and both are marked `disputed`. An answer built on them should
  say the time is not settled.
- **Related messages are linked, not merged.** Message 2 carries out the
  plan in message 1 (`implem`), and the demo is related to the launch plan
  (`see`).

A model's judgment can differ from run to run. The adversarial cases in
[`eval/adversarial`](../../eval/adversarial) measure how often the judge
gets cases like these right.

A host that runs the engine in its own database reads the same answers from
`core_memory.bead_state` and `core_memory.bead_text`; see [Reading in
place](../../docs/guide.md#reading-in-place).
