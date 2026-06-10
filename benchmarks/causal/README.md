# Causal-Chain Reconstruction Benchmark

Status: causal benchmark harness

The LoCoMo-style harness (`benchmarks/locomo_like/`) measures semantic QA — the
game Core Memory has explicitly said it is *not* playing. This benchmark
measures the thesis the project actually makes: **notate, maintain, and retrieve
over causal connections.**

## What it measures

Each case seeds a synthetic history with a **known causal chain** plus one or
more **adversarial distractors** — beads that are the *semantically closest*
match to the query but are **not** on the causal chain. A pure-similarity system
ranks the distractor first; causal traversal must surface the true root cause
instead.

Per-case metrics:

| Metric | Meaning |
|---|---|
| `edge_precision` / `edge_recall` / `edge_f1` | Traversed causal edges vs. the known gold edges |
| `grounding_full` | Traversal reconstructed a path reaching the gold root cause with complete edge recall |
| `root_cause_correct` | Top-ranked causal candidate is the gold root cause |
| `attribution_depth` | Deepest causal path reconstructed |
| **`distractor_survived`** | **The gold root cause outranks every adversarial distractor** |

The aggregate **`distractor_survival_rate`** is the headline one-number metric:
the fraction of adversarial cases where causal traversal beats pure similarity.

## Why this is the right eval

For the `adversarial_distractor` case the pure-semantic ranking is:

```
distractor   score=1.48   <- runbook titled "why was the api returning 500 errors"
mid          score=0.27
outcome      score=0.27
root         score=0.27   <- the TRUE root cause, ranked last
```

…while the causal root-cause ranking is:

```
root         influence=1.0   <- the TRUE root cause, ranked first
mid          influence=0.47
(distractor never appears — it has no causal edges)
```

That inversion — similarity puts the distractor first, causality puts the true
cause first — is the entire argument for a causal memory layer over a reranked
vector store.

## Quick start

Run all checked-in fixtures:

```bash
python -m benchmarks.causal.runner --subset full
```

Run a small local subset:

```bash
python -m benchmarks.causal.runner --subset local
```

Emit a report to file:

```bash
python -m benchmarks.causal.runner --subset full --out benchmarks/reports/causal.json
```

## Design notes

- Fixtures are checked-in synthetic histories with gold causal edges — no
  benchmark answers are hardcoded in retrieval code.
- Histories are materialized through the public write path (`add_bead` +
  agent-judged associations), then queried through `recall(intent="causal")`.
- The benchmark reads `root_cause_attribution.causal_paths[].edges[]` to recover
  the traversed edges and compares them against the known gold edges.
- Distractor survival only counts cases that actually configure distractors, so
  control cases cannot inflate the headline rate.
