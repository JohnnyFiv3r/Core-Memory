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
