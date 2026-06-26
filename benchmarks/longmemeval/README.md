# LongMemEval Benchmark Adapter

Loads a user-supplied [LongMemEval](https://github.com/xiaowu0162/LongMemEval)
JSON or JSONL corpus into Core Memory's shared `BenchmarkAdapter` contract.

The corpus is not vendored. Download the data separately and pass the file path
at runtime.

## Adapter Smoke

```bash
python -m benchmarks.longmemeval --corpus path/to/longmemeval_s.json --limit 1 --pretty
```

The smoke command parses the corpus and converts instances into
`BenchmarkConversation` objects. It does not ingest into Core Memory and does not
make leaderboard claims.

## Evaluation Smoke

```bash
python -m benchmarks.longmemeval --corpus path/to/longmemeval_s.json --limit 1 --eval-smoke --pretty
```

The evaluation smoke replays a bounded set of LongMemEval turns through Core
Memory's lifecycle, scores answer/evidence through the adapter contract, and
still reports `leaderboard_claim: false`.

## Supported Shape

The loader expects the public LongMemEval v1 fields:

- `question_id`
- `question_type`
- `question`
- `answer`
- `question_date`
- `haystack_session_ids`
- `haystack_dates`
- `haystack_sessions`
- `answer_session_ids`

`haystack_sessions` should be a list of sessions, where each session is a list
of `{ "role": "user"|"assistant", "content": "..." }` turns. Turns may include
`has_answer: true`; the loader preserves that flag as metadata only.
