# Observation Ledger benchmark framework

This package is the canonical Stage 0 semantic-evaluation harness. It extends
`benchmarks/contracts.py`; it does not replace the existing benchmark package.

The framework separates case inputs, proposed/accepted gold, and human
adjudications. Runtime authors receive only source events, admissible evidence,
and task input. The selected judge receives those inputs plus candidate output
and accepted gold, but no Core Memory persistence root or writer interface.

Both model selectors are explicit `provider:model` values. The author and
judge may be the same model, different models from one provider, or models from
different providers. There are no defaults or substitutions.

```bash
python -m benchmarks.observation_ledger validate \
  --pack /absolute/or/repo/public-pack --visibility public

python -m benchmarks.observation_ledger adjudicate \
  --pack /path/to/pack --judge-model provider:model --out /path/to/critiques.json

python -m benchmarks.observation_ledger run \
  --pack /path/to/pack \
  --author-model provider:model \
  --judge-model provider:model \
  --out /path/to/report.json

python -m benchmarks.observation_ledger baseline \
  --public-report /path/to/public.json \
  --private-report /path/to/private.json \
  --thresholds /path/to/thresholds.json \
  --out /path/to/baseline.json
```

`validate` never resolves a model and never accesses the network. Missing
selectors, credentials, providers, or prerequisites produce a `blocked`
artifact. Harness shortcuts or gold leakage produce `disqualified`. Framework
or execution failures produce `failed`. Honest scored runs produce
`completed`, even when a product runtime fallback causes a hard-invariant
failure.

The scripted adapters in `testing.py` are exclusively for harness tests. Their
reports record `test_adapter_used: true`, and `baseline` always disqualifies
them.

## Public corpus packs

- `packs/public/observations-statement-request-v1/` — 25 accepted PR-00E
  statement/request observation cases with separate inputs, semantic gold,
  exact evidence spans, frontier critique, human decisions, and checksums.
- `packs/public/observations-goal-decision-v1/` — 25 accepted PR-00F
  goal/decision observation cases covering attribution, conditionality,
  provisionality, authority, completion boundaries, canonical facets, exact
  evidence spans, frontier critique, human decisions, and checksums.
