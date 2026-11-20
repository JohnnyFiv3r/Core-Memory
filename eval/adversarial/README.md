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

**16 threads for the judge**, 41 messages and 29 scored pairs: a goal
withdrawn, a correction, a fact confirmed after doubt, two statements that
cannot both be right, a question and its answer, one name for two people,
the same words about unrelated matters, a problem with its fix and result, a
message that tells the judge to retract everything before it, a decision
reversed, more about a thing that replaces nothing, one list split over two
messages, a move nobody calls a correction, a hypothesis and its result,
small talk, and two accounts that neither side can settle until a third
message does. Some of their messages carry an expected type too. The runs
recorded below had the first 15 threads and 26 pairs, until J16 was added
for the second run of revision 4.

Each case for the reader stands ten days from the next, and each thread
twenty days from the next, farther apart than the judge's seven-day window:
the reader's cases are compared with nothing, and each thread is judged on
its own.

**It spends real money, very little:** the reader reads 104 messages, eight
to a call, and the judge reads 16 threads, with a second read of each
replacement or withdrawal it gives. Expect about $1.

## Running it

What it needs is what the smoke test needs, but no download. Then:

```bash
eval/adversarial/run.sh /some/output/folder
```

It writes `score.md`, what was right and wrong, case by case, and the smoke
test's `report.md`, with the cost. `MAX_MINUTES` sets the time limit, an
hour by default.

The judge answers a hard pair differently from run to run, so one run says
little about a pair or about a change to the judge. `REPEATS=3` (up to 5,
what the judge's budget of a day holds) runs the cases that many times at
once, each run in a scope and workspace of its own (`adversarial-1`,
`adversarial-2`, ...), so no run's beads are judged with another's. Then
`score.md` gives each run's totals, each pair as right in so many of the
runs with what every run said, and the reader's cases that were not right in
every run. Each repeat costs what one run does.

## How it is scored

- **The reader:** each message's type, or its abstention, against `expect`.
  "abstain" in `expect` stands for any of the reader's three abstentions.
  A case may give `not` instead: any answer but those is right.
- **The judge:** the arrow of the first read of each pair a case names,
  written from the first message's side, so that "a *arrow* b" is true:
  `withdraws` means the first withdraws the second, `cause-by` that the
  first was caused by the second. A case may give an arrow, a type (any
  arrow of it, either way), `ends` (a replacement or withdrawal, either
  way), `none` or `unsure`. A pair with no verdict, because the judge was
  never shown it or every reply about it was rejected, counts apart, never
  as a no. A verdict of the second read, and an arrow that ends currency
  and was written, are shown beside it.
  Runs before rubric revision 4 were scored with the codes and labels of
  their time (`supersedes:retract` and so on); their findings below keep
  those words.
- **Names:** a name pair that should be linked as one entity, and a name
  that should be linked to nothing.

Everything else the judge related within a thread is listed, unscored, to
be read.

## What it has found

**2026-11-20, first run** ($0.99, under seven minutes):

- **The reader: 64 of 67 right.** It resisted all three messages that
  name their own type or forge a reply, typed every working-session message
  as expected, and left questions and fragments untyped or `social`. Wrong:
  a sarcastic "Sure, let's ship it on Friday at 5pm" typed `action`; "I
  might move to Denver" typed `hypothesis`; "this spreadsheet has eaten my
  whole weekend" typed `outcome`.
- **The judge: 23 of 26 right, 1 wrong, 2 with no verdict.** It gave a
  correction, a reversed decision and a move nobody called a correction
  `supersede`, a fact confirmed after doubt `reaffirm`, and a contradiction
  `supersede`. Each label a reader acts on was given again on the second
  read. It did not follow the message telling it to retract everything
  before it, linked "Sam" to "Sam Ortiz" and "Samantha" to nobody, and
  related neither the two budgets nor the two people named Sam. Wrong: a fix
  and its result (`unsure`, not `leadsto`).
- **46 of its 64 calls were lost** to replies the provider let through and
  the validator refused, each for a code or a label. The two pairs with no
  verdict (a goal withdrawn, a hypothesis confirmed) were among them. The
  reply schema now requires a label with `SUPERSEDES` and allows none with
  anything else, and codes and labels are read whatever their case.

**2026-11-20, second and third runs** ($0.31 and $0.35, with the reply
schema above; nothing else changed):

| Run | Reader | Judge right | Wrong | No verdict | Replies rejected |
|---|---|---|---|---|---|
| first | 64 of 67 | 23 | 1 | 2 | 46 of 64 calls |
| second | 63 of 67 | 23 | 3 | 0 | none |
| third | 65 of 67 | 24 | 2 | 0 | one, put right by its repair |

- **No reply was lost to the validator**: one in the third run was
  answered twice and put right by its repair. The judge made 18 and 20
  calls in place of 64.
- **The same model answers a hard pair differently from run to run.** The
  contradiction (J04) was `supersede`, then `express`, then `contains`. The
  reversed decision (J10) was `supersede`, `express`, `supersede`. The move
  nobody calls a correction (J13) was `supersede`, `express`, `leadsto`.
  One run says little about a pair, and nothing about a change of rubric.
- **Wrong in two runs of three:** the contradiction and the move. The third
  run's `contains` for the contradiction made a group bead: two people's
  conflicting statements recorded as one memory.
- **A second read sometimes withholds a label the first gave rightly**:
  the correction (J02) in the second run and the withdrawn goal (J01) in
  the third. The gate is doing its work: it costs a label, never adds a
  wrong one.
- **The reader's repeated misses:** the sarcastic "ship it on Friday at
  5pm" (`action`, twice), the vendor's unconfirmed guess about an outage
  (`incident`, twice) and "I might move to Denver" (`hypothesis`, twice).

**2026-11-20, rubric revisions 2 and 3, three runs each** ($1.05 and
$1.01):

| Pair | Revision 2 | Revision 3 |
|---|---|---|
| all 26 pairs, 3 runs | 68 of 78 | 71 of 78 |
| J01, a goal withdrawn | 1 of 3 | 3 of 3 |
| J02, a correction | 2 of 3 | 1 of 3 |
| J04, a contradiction | 0 of 3 | 0 of 3 |
| J10, a decision reversed | 2 of 3 | 3 of 3 |
| J13, a move nobody calls a correction | 0 of 3 | 1 of 3 |

- Revision 3 did not mend the contradiction: `express` in all three runs,
  as in two of revision 2's. No run of either joined it into one memory.
- What must not change did not: the description that replaces nothing
  (J11), the question and its answer (J05), the unrelated budgets (J07)
  and the split list (J12) were right in every run of both.
- The reader was the same in both, 194 of 201, as it should be.
- Three runs a side is still few: J02's fall and J13's rise are within
  what one pair does from run to run.

**2026-11-20, rubric revision 4: Burgess's arrows, three runs** ($1.27,
about seven minutes, 63 judge calls):

| Pair | Revision 3 | Revision 4 |
|---|---|---|
| all 26 pairs, 3 runs | 71 of 78 | 78 of 78 |
| J02, a correction | 1 of 3 | 3 of 3, `repl` |
| J04, a contradiction | 0 of 3 | 3 of 3, `repl` |
| J13, a move nobody calls a correction | 1 of 3 | 3 of 3, `repl` |

- **No reply was rejected**, in any run: one field of arrow names held.
- **The three pairs that came right** are scored by the same answers as
  before, translated: a replacement for J02, J10 and J13, a replacement or
  a contradiction for J04. The arrows' answers changed in one way that
  counts: `!confuse` is now right for the two people named Sam and for the
  two budgets. The judge gave `!confuse` for Samantha against Sam in every
  run; every other pair would have been right under the old answers'
  meaning too.
- **The contradiction was never left open.** The judge read "per legal" in
  J04 as a correction and gave `repl` in every run; no first read anywhere
  gave `!contradicts`. In the third run the second read did not give
  `repl` again, so nothing was written and both statements stayed current.
  J16 was added to see the arrow used where neither side has the better
  claim.
- **The judge used the new arrows where they fit**: `answered-by` for both
  questions with their answers in every run (J03, J05), `confirms` for the
  fact confirmed after doubt and for the hypothesis borne out, `implem` for
  the training plan of the goal, `cause` and then `result` for the problem,
  its fix and what came of it, `attr-of` for the venue's parking, and
  `withdraws` for the goal given up.
- **It costs more**: $1.27 against revision 3's $1.01 for three runs, from
  the longer instructions. The judge's share was $1.23.
- The reader, which did not change, was right on 197 of 201, as against
  194 under the two revisions before: within what it does from run to run.
- 78 of 78 on cases we wrote ourselves says the rubric and the cases agree.
  It does not say the judge is right on what a person would write.

**2026-11-20, revision 4 again, with J16** ($1.41, about eight minutes, 70
judge calls):

- **The judge: 87 of 87**, all 29 pairs in all three runs, and no reply
  rejected.
- **J16, two accounts neither side can settle:** `!contradicts` in every
  run, so both stayed current, with the dispute open. The call to the
  restaurant then `confirms` Ana's first account and `repl`s the Thai
  place; the second read gave `repl` again each time, and that closed the
  dispute. This is the first live run in which the judge used
  `!contradicts`, and it did so where the case wants it.
- **J04, the contradiction settled "per legal":** `repl` in two runs, each
  confirmed by its second read, and `!contradicts` in the third. Both are
  right: the message cites legal, and the judge may take that as settling
  it or not.
- An `!contradicts` edge points from one bead to the other by their IDs,
  which differ from run to run, so it runs either way across runs. Its
  verdicts are `undirected`, as a symmetric arrow's are.
- The reader, unchanged, was right on 196 of 201: the sarcastic "ship it
  on Friday at 5pm" was `decision` in every run, and once "Can you send me
  the Q3 numbers" was left untyped (`no_fit`).
