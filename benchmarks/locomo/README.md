# LoCoMo Benchmark Adapter

Evaluates Core Memory recall quality against the [LoCoMo](https://github.com/snap-research/locomo)
long-conversation memory benchmark.

## Quick start

```bash
# Obtain the corpus (see Corpus section below)
python -m benchmarks.locomo --corpus path/to/locomo10.json --smoke
```

## Corpus

LoCoMo is not included in this repo due to licensing. Obtain `locomo10.json` from
the [LoCoMo authors](https://github.com/snap-research/locomo) and place it at any
path you choose — pass it to `--corpus`.

Expected statistics: 10 samples, ≥1980 QA items, ≥5800 turns.

## Usage

```
python -m benchmarks.locomo \
  --corpus locomo10.json \
  [--smoke]             # 2 conversations, 5 QA each
  [--limit N]           # max conversations
  [--max-qa N]          # max QA per conversation
  [--k 10]              # retrieval k
  [--out report.json]   # write JSON report
  [--pretty]            # pretty-print JSON to stdout
```

## Design invariants

- **No gold answer pollution.** Gold answers (`expected_answer`) and gold evidence
  (`gold_evidence` dia_ids) exist only in Python QA objects. They are never
  written to the benchmark temp dir and never passed to `recall()`.

- **dia_id space scoring.** Bead IDs are non-deterministic across ingestion runs.
  All evidence scoring happens in dia_id space using a `dia_id→bead_id` map
  built at ingest time by reading `source_turn_ids` from the bead index.

- **k is constant.** The retrieval `k` is a fixed value (default 10), not derived
  from `len(gold_evidence)`.

- **Category 5 excluded.** 444/446 category-5 questions have broken answer keys
  in the public corpus. Category 5 is excluded from all official evaluation.

- **Isolation.** Each conversation is evaluated in a fresh `tempfile.mkdtemp()`
  directory. No state leaks across conversations.

- **`process_turn_finalized` directly.** The `emit_turn_finalized` path has a
  guard (`should_emit_memory_event`) that blocks synchronous writes outside
  runtime contexts. The benchmark uses `process_turn_finalized` directly.

## Output format

The JSON report mirrors the structure of `benchmarks/locomo_like/` reports and
adds LoCoMo-specific fields:

```json
{
  "schema_version": "locomo_runner.v1",
  "run_at": "<iso8601>",
  "git_sha": "<8-char>",
  "config": { "k": 10, "excluded_categories": [5], ... },
  "aggregate": {
    "total_cases": 1800,
    "cases_with_evidence": 1400,
    "overall": {
      "answer_f1_mean": 0.42,
      "recall@1_mean": 0.31,
      "recall@5_mean": 0.48,
      "mrr_mean": 0.37,
      "hit_any_rate": 0.55
    },
    "by_category": {
      "1": { "case_count": 450, "answer_f1_mean": 0.38, ... },
      "2": { ... },
      "3": { ... },
      "4": { ... }
    }
  },
  "conversations": [ ... ]
}
```

## Extending to other benchmarks

The `BenchmarkAdapter` protocol in `benchmarks/contracts.py` defines the interface
any dataset adapter must satisfy. Implement `load_conversations()`, `score_answer()`,
and `score_evidence()` for your dataset, then wire it into a runner using the same
lifecycle pattern as `benchmarks/locomo/runner.py`.
