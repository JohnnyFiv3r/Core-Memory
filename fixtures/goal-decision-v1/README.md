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

`crates/core-memory-sidecar/tests/goal_decision.rs` checks that every quote
matches its byte range and drives all 25 cases through the classifier with
the gold answers. Both run in CI.

To score a real model, put a `[classifier]` table and its `[[binding]]` in a
file and run the ignored test. The binding's `scopes` are replaced with the
fixture scope. At the default batch size of 8 it makes four model calls,
plus any repairs.

```bash
CORE_MEMORY_EVAL_BINDING=binding.toml \
    cargo test -p core-memory-sidecar --test goal_decision -- --ignored --nocapture
```

The run prints every miss with its rationale. It fails if any case is left
undecided or gets a forbidden type.

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
