# LOCOMO-style baselines and positioning

Status: public positioning artifact for Gap #6. This page separates **Core Memory's
in-repo deterministic LOCOMO-like evidence** from **public LOCOMO/LoCoMo benchmark
numbers reported by other projects**. Do not compare the local proxy score as if it
were a full LOCOMO leaderboard result.

## What Core Memory proves today

Core Memory has a reproducible local benchmark harness under
`benchmarks/locomo_like/` for the failure modes that matter to long-horizon agent
memory:

- current-state factual recall
- historical/as-of recall
- contradiction/update handling
- causal/mechanism recall
- entity/coreference recall
- preference, identity, policy, commitment, and condition recall

Latest local verification on `b4e7167`:

```bash
python -m benchmarks.locomo_like.runner --subset local --out /tmp/core-memory-locomo-local.json
```

Observed summary:

| Metric | Value |
| --- | ---: |
| Cases | 6 |
| Passed | 6 |
| Failed | 0 |
| Accuracy | 1.0000 |
| Mean latency | 89.976 ms |
| p95 latency | 168.294 ms |
| Estimated total tokens | 198 |
| Estimated mean tokens / case | 33 |
| Semantic mode | `degraded_allowed` |
| Backend mode observed | `degraded_lexical` |
| Warning | `semantic_backend_unavailable_degraded` |

Interpretation: this is **behavioral proof for Core Memory's retrieval contract and
benchmark plumbing**, not a claim that Core Memory has a public full-LOCOMO score yet.
The important product proof is that the benchmark records pass/fail, bucket accuracy,
latency, queue state, backend mode, token estimates, dreamer correlation, and optional
myelination observability in one machine-readable report.

## Public comparison numbers

The following numbers are third-party/publicly reported benchmark results. They are
included to anchor Core Memory's positioning language and to prevent hand-wavy claims.

### Mem0 paper / evaluation repository

Sources:

- `https://raw.githubusercontent.com/mem0ai/mem0/main/evaluation/README.md`
- `https://arxiv.org/abs/2504.19413`
- `https://arxiv.org/html/2504.19413`

Mem0 reports LOCOMO category-level LLM-as-a-Judge scores:

| Method | Single-hop J | Multi-hop J | Open-domain J | Temporal J | Overall J |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mem0 | 67.13 | 51.15 | 72.93 | 55.51 | 66.88 |
| Mem0-Graph / `Mem0^g` | 65.71 | 47.19 | 75.71 | 58.13 | 68.44 |
| Zep | 61.70 | 41.35 | 76.60 | 49.31 | 65.99 |
| LangMem | 62.23 | 47.92 | 71.12 | 23.43 | 58.10 |
| OpenAI memory | 63.79 | 42.92 | 62.29 | 21.71 | 52.90 |
| A-Mem* rerun | 39.79 | 18.85 | 54.05 | 49.91 | 48.38 |

Mem0's paper also reports baseline RAG overall J by chunking strategy:

| RAG setting | Overall J |
| --- | ---: |
| top-1, 128-token chunks | 47.77 |
| top-1, 256-token chunks | 50.15 |
| top-1, 512-token chunks | 46.05 |
| top-1, 1024-token chunks | 40.74 |
| top-1, 2048-token chunks | 37.93 |
| top-1, 4096-token chunks | 36.84 |
| top-1, 8192-token chunks | 44.53 |
| top-2, 128-token chunks | 59.56 |
| top-2, 256-token chunks | 60.97 |
| top-2, 512-token chunks | 58.19 |
| top-2, 1024-token chunks | 50.68 |
| top-2, 2048-token chunks | 48.57 |
| top-2, 4096-token chunks | 51.79 |
| top-2, 8192-token chunks | 60.53 |
| full-context | 72.90 |

Positioning note: Mem0's strongest reported result is a full benchmark score, while
Core Memory's current public artifact is an in-repo proxy harness. Core Memory should
claim stronger **grounding/agent guidance/retrieval observability** today, and reserve
full leaderboard claims until the `--subset full --preload-turns ...` path is run on a
licensed/available LOCOMO trace.

### Memanto evaluation repository / paper

Sources:

- `https://raw.githubusercontent.com/moorcheh-ai/memanto-evaluation/main/README.md`
- `https://arxiv.org/abs/2604.22085`

Memanto reports LOCOMO category-level scores:

| Method | Single-hop | Multi-hop | Open-domain | Temporal | Overall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hindsight (Gemini-3) | 86.17% | 70.83% | 95.12% | 83.80% | 89.61% |
| Memanto (Gemini-3) | 78.72% | 70.83% | 92.39% | 85.36% | 87.08% |
| Hindsight (OSS-120B) | 76.79% | 62.50% | 93.68% | 79.44% | 85.67% |
| Hindsight (OSS-20B) | 74.11% | 64.58% | 90.96% | 76.32% | 83.18% |
| Memobase v0.0.37 | 70.92% | 46.88% | 77.17% | 85.05% | 75.78% |
| Zep | 74.11% | 66.04% | 67.71% | 79.79% | 75.14% |
| Mem0-Graph | 65.71% | 47.19% | 75.71% | 58.13% | 68.44% |
| Mem0 | 67.13% | 51.15% | 72.93% | 55.51% | 66.88% |
| LangMem | 62.23% | 47.92% | 71.12% | 23.43% | 58.10% |
| OpenAI | 63.79% | 42.92% | 62.29% | 21.71% | 52.90% |

Positioning note: Memanto's public message is simple adoption plus benchmark wins.
Core Memory's counter-positioning should not pretend to have Memanto's full public
score yet. The honest win is that Core Memory exposes a richer agent-facing contract:
answers carry source surfaces, citations, grounding state, answer policy, queue/backend
observability, and feature-gated retrieval behavior rather than a single opaque score.

## Core Memory feature-flag matrix for LOCOMO-style runs

These are the flags that define the benchmarked Core Memory retrieval shape. Keep this
matrix in sync with `benchmarks/locomo_like/runner.py`.

| Feature / flag | Benchmark default | Purpose | Public positioning relevance |
| --- | --- | --- | --- |
| `CORE_MEMORY_CLAIM_LAYER` | `1` | Enables claim extraction/indexing. | Adds structured current/historical facts beyond raw transcript chunks. |
| `CORE_MEMORY_CLAIM_EXTRACTION_MODE` | `heuristic` | Uses deterministic local claim extraction in the fixture harness. | Keeps CI/local evidence reproducible and keyless. |
| `CORE_MEMORY_CLAIM_RESOLUTION` | `1` | Enables claim supersession/currentness resolution. | Directly targets contradiction/update and temporal truth cases. |
| `CORE_MEMORY_CLAIM_RETRIEVAL_BOOST` | `1` | Boosts claim-state retrieval candidates. | Makes answer policy prefer durable current facts when available. |
| `CORE_MEMORY_PREVIEW_ASSOC_PROMOTION` | `1` | Enables association promotion preview path. | Exercises relation-aware retrieval depth. |
| `CORE_MEMORY_PREVIEW_ASSOC_ALLOW_SHARED_TAG` | `1` | Allows shared-tag association promotion in the preview path. | Improves recall on related memory clusters while remaining gated. |
| `CORE_MEMORY_CANONICAL_SEMANTIC_MODE` | `degraded_allowed` | Allows keyless/local degradation when semantic backend is missing. | Matches simple adoption: first run works without API-key setup. |
| `CORE_MEMORY_VECTOR_BACKEND` | `local-faiss` | Requests local FAISS backend when dependencies are present. | Gives a local scaling path; reports degraded mode when unavailable. |
| `CORE_MEMORY_MYELINATION_ENABLED` | `0` by default; `1` with `--myelination on` | Enables reinforcement/myelination behavior for comparison runs. | Separates baseline retrieval from experimental strengthening signals. |
| `--async-profile` | `drain_before_query` | Drains/observes async queues before query. | Proves async transcript/semantic work is visible rather than hidden. |
| `--myelination compare` | opt-in | Runs paired off/on reports and emits a comparison block. | Provides an experiment lane without contaminating baseline numbers. |

## Recommended public claim language

Safe today:

> Core Memory includes a reproducible LOCOMO-style harness that passes the local proxy
> suite across current-state, historical/as-of, contradiction, causal, coreference, and
> preference/policy cases while reporting latency, token estimates, queue state,
> backend mode, grounding/citation state, dreamer correlation, and optional myelination
> observability.

Safe competitor positioning:

> Public LOCOMO numbers from Mem0 and Memanto set the external target: Mem0 reports
> 66.88 overall J, Mem0-Graph 68.44, and baseline RAG up to 60.97 overall J; Memanto
> reports 87.08 overall. Core Memory's current differentiator is not a full public
> leaderboard score yet, but a deeper agent-facing retrieval contract and a benchmark
> path that can be run locally, in CI, and against full preloaded traces.

Do **not** claim yet:

- Core Memory beats Mem0 or Memanto on full LOCOMO.
- Core Memory has a full LOCOMO score from the local six-case fixture pack.
- Keyless degraded lexical mode is equivalent to production semantic retrieval.

## Next evidence step

To graduate from positioning artifact to leaderboard claim, run:

```bash
python -m benchmarks.locomo_like.runner --subset full --preload-turns /path/to/locomo_turns.jsonl --out reports/locomo-full.json
```

Then publish the resulting JSON/stdout artifact with the exact commit, backend mode,
semantic provider, model/judge settings, and any warnings.
