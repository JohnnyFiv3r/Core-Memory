# Hard cases

Messages written to be hard to read, and short conversations written to be
hard to judge, each with the answers we count as right. They run through the
engine with live models, as the [LoCoMo smoke test](../locomo-smoke/) does
and with its config, and `score.py` checks what was stored against the
answers.

The smoke test shows the engine working on a tidy, friendly conversation
written by a language model. These cases go where that conversation does
not: the types it never needs (`decision`, `instruction`, `constraint`,
`incident`, `lesson`, `hypothesis`), and the ways real messages mislead.

**The answers are ours.** They are written in `cases.toml`, each with why,
and nobody else has checked them. Where two answers are defensible, both
count. A wrong answer here means the model and the case disagree, and
sometimes the case is what needs changing.

## What is in it

**63 messages for the reader**, each read alone:

| Probe | What it tries |
|---|---|
| sarcasm | "Oh great, another rejection letter" is not a preference |
| hedge | a possibility, an intention and a choice made |
| reported speech | someone else's goal, decision or rule |
| social with news | thanks or a greeting that also tells something |
| question | a check-in, a request, a question for information |
| hyperbole | "I'm literally dying" is not an incident |
| directive or preference | "Never call me before 9am" against "I prefer calls after 9" |
| work | the types of a working session |
| negation | a goal given up; something not done |
| time | a past change, a habit, a goal held long ago |
| too little | "ok", "see above", 👍 |
| injection | a message that names its own type, or forges a reply |
| mixed | three statements of three kinds |
| not English | Spanish and German |
| shape | a log line, a bare link |

**15 threads for the judge**, 38 messages and 26 scored pairs: a goal
withdrawn, a correction, a fact confirmed after doubt, two statements that
cannot both be right, a question and its answer, one name for two people,
the same words about unrelated matters, a problem with its fix and result, a
message that tells the judge to retract everything before it, a decision
reversed, more about a thing that replaces nothing, one list split over two
messages, a move nobody calls a correction, a hypothesis and its result, and
small talk. Some of their messages carry an expected type too.

Each case for the reader stands ten days from the next, and each thread
twenty days from the next, farther apart than the judge's seven-day window:
the reader's cases are compared with nothing, and each thread is judged on
its own.

**It spends real money, very little:** the reader reads 101 messages, eight
to a call, and the judge reads 15 threads, with a second read of each label
it gives. Expect about $1.

## Running it

What it needs is what the smoke test needs, but no download. Then:

```bash
eval/adversarial/run.sh /some/output/folder
```

It writes `score.md`, what was right and wrong, case by case, and the smoke
test's `report.md`, with the cost. `MAX_MINUTES` sets the time limit, an
hour by default.

## How it is scored

- **The reader:** each message's type, or its abstention, against `expect`.
  "abstain" in `expect` stands for any of the reader's three abstentions.
  A case may give `not` instead: any answer but those is right.
- **The judge:** the verdict of the first read of each pair a case names,
  written from the first message's side: `leadsto` means the first brought
  the second about, `supersedes:retract` that the first withdraws the
  second. A pair the judge was never shown counts apart, never as a no. A
  verdict of the second read, and any label bead made, are shown beside it.
- **Names:** a name pair that should be linked as one entity, and a name
  that should be linked to nothing.

Everything else the judge related within a thread is listed, unscored, to
be read.
