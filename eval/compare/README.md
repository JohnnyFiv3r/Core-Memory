# Judges compared

`compare.py` puts what two or more judges said about the same messages side
by side, pair by pair, for a person to read. It takes the run folders that
the [smoke test](../locomo-smoke/) and the [hard cases](../adversarial/)
write:

```bash
python3 eval/compare/compare.py opus-1=out/opus-1 opus-2=out/opus-2 \
    sonnet-1=out/sonnet-1 sonnet-2=out/sonnet-2
```

Runs line up by message, so they may come from different databases. An
export of the hard cases run with `REPEATS` is one run per scope.

It reports, for every two runs, over the pairs both read:

- **related**: whether both said the two messages are related, or both said
  they are not (`none` or `unsure`);
- **type**: the same arrow type, or both `none`, or both `unsure`, and how
  far that agreement is beyond chance (Cohen's κ);
- **arrow**: the same arrow, of the pairs both related;
- **ended**: the arrows that end currency (`repl`, `withdraws`) that both
  wrote, of those either wrote. These change what a reader takes as
  current, so they matter most.

Then the same, summed within a model and across models. **Read the two
together.** One judge answers a hard pair differently from run to run, so
two runs of one model never agree completely. Agreement across models as
high as within a model means the models do not differ by more than the
noise; a gap is what they differ by. It ends with the pairs where one
model ended a statement and the other did not, and where every run of one
model related two messages and no run of the other did, with their text.

There are no right answers on a real conversation: the comparison says
whether the models agree, not which one is right. The hard cases have
answers, and `score.md` scores each judge against them.

## Comparing Sonnet 5.5 with Opus 5.5 as the judge

What we run, each in a fresh database, with the reader unchanged:

1. The hard cases, `REPEATS=3`, with `JUDGE_MODEL=claude-sonnet-5-5` at
   effort `medium` and again at `high`. Opus's score is the baseline:
   `score.md` scores each judge against the same answers.
2. LoCoMo `conv-26` twice with Opus 5.5 and twice with Sonnet 5.5 at the
   effort that did better in step 1, then `compare.py` on the four.

Sonnet 5.5's effort levels are its own, recalibrated: `medium` on Sonnet is
not `medium` on Opus, so it is tried at two.

## What it has found

**2026-11-20, Sonnet 5.5 against Opus 5.5** ($5.04 for all seven runs,
with the hard cases' results in [`eval/adversarial`](../adversarial/)):
LoCoMo `conv-26`, judged twice by each, with Sonnet at effort `medium`, its
better setting on the hard cases.

| | Opus 5.5 | Sonnet 5.5 |
|---|---|---|
| Cost per conversation, reader included | $1.37, $1.35 | $0.76, $0.83 |
| Judge calls | 43, 42 | 67, 83 |
| Wall time | 6m 15s, 6m 06s | 3m 11s, 3m 43s |
| Group beads (memories joined) | 0, 3 | 4, 11 |
| Name links | 35, 29 | 8, 9 |

| Agreement | Opus ×2 | Opus · Sonnet | Sonnet ×2 |
|---|---|---|---|
| Type, beyond chance (κ) | 0.79 | 0.58–0.61 | 0.76 |
| The same arrow, of pairs both related | 91% | 78% | 86% |

- **The models differ by more than a model differs from itself.** Each agrees
  with its own other run better than with the other model. Sonnet relates
  pairs as `answers` and `sim` more often, and as `see` much less.
- **Sonnet's groups disagree from run to run.** It made 4 in one run and 11
  in the other.
- **Sonnet's name links mostly fail.** Many of its replies about names were
  rejected (`name`, 10 and 17), and that is most of why it needed more calls
  than Opus.
- **Neither judge ended a statement** on this friendly conversation. That
  is where the hard cases show the difference.
- Both judged every bead. Sonnet costs about 42% less per conversation, and
  about $8 less over all of LoCoMo.
