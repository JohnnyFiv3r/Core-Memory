# Goal/decision cases, v1

25 short synthetic conversations, each labelled with the type its one chunk
should get: 13 `goal` and 12 `decision`. They test the two confusions that
matter most for this pair:

- stating a goal is not reaching it, so a goal is never typed `outcome`;
- deciding is not doing the work decided on, so a decision (including an
  approval, a delegation or a deferral) is never typed `action`.

The cases cover tentative, conditional, attributed, corrected, hierarchical
and constrained goals, and provisional, deferred, delegated, reported,
conditional, reversible, approval-only, rejected and partly resolved
decisions.

## Files

- `cases.jsonl`: one case per line.
  - `event` is the SourceEvent to admit.
  - `label` is the preferred type, and `acceptable` lists the types that
    also count as correct. Two reported cases also accept `statement`.
  - `forbidden` lists the confusions above.
  - `rationale` explains the label.
  - `evidence` gives byte ranges into the text that support the label,
    with their exact quotes.
- `vocabulary.toml`: the types the cases are scored against, with
  definitions that state both distinctions.

## Running it

```bash
cargo test -p core-memory-store-postgres --features postgres-tests --test fixture
```

The test admits every case and has a stand-in model read it. The stand-in
answers from the labels, so the test measures no model. It checks the scorer,
and that a label and its evidence pass through the engine unchanged: every
evidence quote must come back as the byte range the case gives. A stand-in
that makes the two confusions must be caught, and one that abstains must be
counted as neither right nor wrong.

Measuring a model means reading the same cases with a configured reader,
which costs model calls. That is done by hand, not by the test suite.

## Where it came from

Converted from the 1.x observation-ledger pack
`public-observations-goal-decision-v1`: PR #440, branch
`codex/pr-00f-goal-decision-observations` at `c4d48bea`. The source files'
SHA-256 digests:

| File | SHA-256 |
|---|---|
| `inputs/cases.json` | `d6f8ab8fb063ab4a7203a4e6abed598d7610186134de4d631f57cdb3e720a0c9` |
| `gold/gold.json` | `0027d25d35dab7b8e574f3f6cbbd3681f50084d0e01cb474eb8ff47cac75b9d5` |

Kept from each case: the event text and time, the preferred and acceptable
labels, the label rationale, the evidence spans and the behavioral buckets.
The `forbidden` types restate the pack's rule that goals are never upgraded
into completed results and decisions are never upgraded into executed
actions.

Dropped:

- the titles, summaries, assertions, facets and propositions the pack
  expected an annotator to write, because the engine writes no generated
  text into a record;
- the adjudication records.

The cases are too few, and too synthetic, to measure a classifier on real
traffic. They are a regression check for this one distinction.
