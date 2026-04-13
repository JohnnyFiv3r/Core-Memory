# Demo Upgrade PRD — Non-Deployment Scope Status (2026-04-13)

## Verdict

Non-deployment demo-upgrade scope is complete on branch `feat/benchmark-temporal-entity-retrieval`.

## Delivered surfaces

- Chat + last-answer diagnostics summary
- Beads / Associations / Graph (clickable provenance)
- Claims (slot detail, history, timeline/update events, `as_of` temporal replay)
- Entities (registry + merge proposals + suggest/accept/reject adjudication)
- Runtime (queue totals + per-queue breakdown, semantic policy/connectivity, flush history)
- Benchmark studio (isolated runs, per-bucket cards, fail explorer, myelination compare, run history, cross-run deltas)
- Rolling-window panel

## Key API surfaces now present

- `GET /api/demo/state`
- `GET /api/demo/claims`
- `GET /api/demo/claim-slot/{subject}/{slot}`
- `GET /api/demo/entities`
- `POST /api/demo/entities/merge/suggest`
- `POST /api/demo/entities/merge/decide`
- `GET /api/demo/runtime`
- `GET /api/demo/bead/{id}`
- `GET /api/demo/bead/{id}/hydrate`
- `POST /api/benchmark-run`
- `GET /api/demo/benchmark/last`
- `GET /api/demo/benchmark/history`
- `GET /api/demo/benchmark/compare/{left_run_id}/{right_run_id}`

## Core non-deployment outcomes

- Benchmark contamination removed (isolated root modes)
- Session-aware continuity injection aligned
- Claims/temporal/entity/runtime observability moved into first-class demo read model
- Multi-run benchmark history + compare added for evaluative trust
- Entity merge review flow surfaced in UI (reviewable, auditable interactions)

## Validation

- Full test suite passing in branch context:
  - `python3 -m unittest discover -s tests -q`
  - latest observed: `Ran 713 tests, OK (skipped=36)`

## Scope explicitly excluded from this completion report

- Deployment topology implementation (Render/Vercel/Supabase wiring)
- Infrastructure-as-code and hosted environment bootstrap
