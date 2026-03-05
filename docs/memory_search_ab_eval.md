# A/B Evaluation Plan: Typed Memory Search vs Existing memory_reason

Branch: `memory-skill-typed-search-ab`

## Goal
Evaluate whether the typed tool boundary (`core.memory_search`) improves reliability and inspectability over the current free-form `memory_reason` path.

## Compared Systems

- **A (baseline):** `core_memory.tools.memory_reason.memory_reason`
- **B (candidate):** `core_memory.tools.memory_search.search_typed`

## Query Set
Use `eval/fixtures/paraphrase_kpi_pack.json` phrasings as the shared query source.

## Metrics

1. `ok_rate` — fraction of successful calls
2. `result_count_avg` — average number of returned candidates
3. `chain_count_avg` — average chain count (where available)
4. `confidence_high_rate` (B only)
5. `suggested_next_distribution` (B only)
6. `anchor_presence_rate` (B only: incident/topic snapped present)
7. `non_causal_why_rate` (A only route-shape diagnostic)

## Acceptance (initial)

- B `ok_rate` >= A `ok_rate`
- B `anchor_presence_rate` >= 0.60
- B `confidence_high_rate` >= 0.40 on causal/what_changed families
- B should not emit brittle warnings for >50% of queries

## Commands

```bash
# Existing path metrics
.venv/bin/python eval/paraphrase_eval.py > /tmp/paraphrase_eval.json

# Typed search path smoke
.venv/bin/python eval/memory_search_smoke.py

# Side-by-side harness
.venv/bin/python eval/memory_search_ab_compare.py > /tmp/memory_search_ab_compare.json
```

## Notes

- This is a deterministic A/B check, not a model-prompt benchmark.
- Keep query set fixed for each comparison run.
- Prefer small, explainable deltas between runs.
