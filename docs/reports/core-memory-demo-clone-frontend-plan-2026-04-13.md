# Plan: Clone-Based `core-memory-demo` Repo with Separate Frontend Path

## Intent

Preserve OSS clarity in `Core-Memory` while shipping a production demo stack separately.

- Keep `Core-Memory` as canonical engine/library OSS repo.
- Create a separate repo (`core-memory-demo`) that consumes Core-Memory and hosts deploy-specific glue.
- Port current non-deployment demo capabilities into the new repo as baseline parity.

## Repository strategy

1. Create new repo: `core-memory-demo` (standalone, not long-lived fork).
2. Add `Core-Memory` dependency pinned by tag/commit.
3. Copy demo UI/API contract behavior, not internal implementation sprawl.
4. Keep reusable engine/runtime fixes upstream in `Core-Memory`.

## Target architecture

- Frontend: standalone web app (separate path, deployable to Vercel)
- Backend API: FastAPI service (deployable to Render)
- Durable data: Supabase-backed persistence path

## Feature parity baseline to port

Port these demo capabilities first (from current branch):

- Inspector surfaces: Beads, Associations, Graph, Claims, Entities, Runtime, Benchmark, Rolling window
- Claims: temporal `as_of`, slot detail, history/timeline
- Entities: merge proposal suggest/accept/reject
- Runtime: queue breakdown, semantic policy/connectivity, flush history
- Benchmark: isolated run modes, compare mode, multi-run history, cross-run deltas
- Bead hydration endpoint + modal drilldown

## Implementation phases

### Phase A — Bootstrap clone repo

- Scaffold `core-memory-demo` with:
  - `frontend/`
  - `backend/`
  - `docs/`
  - CI + lint/test tasks
- Pin Core-Memory dependency in backend environment.

### Phase B — API contract stabilization

- Freeze and document demo API contract in `core-memory-demo/docs/api-contract.md`.
- Ensure backend implements the same endpoint family currently used by demo UI.
- Add contract tests to prevent drift.

### Phase C — Frontend separation

- Move UI from server-rendered file to standalone frontend app.
- Add env-based API base URL (`VITE_API_BASE_URL` or equivalent).
- Keep tab model + diagnostics behavior consistent with baseline.

### Phase D — Durable storage path

- Add Supabase-backed persistence adapter for demo-specific state/history.
- Preserve benchmark run-history and compare functionality under durable storage.
- Keep graceful fallback behavior for local development.

### Phase E — Deployment

- Render service config for backend.
- Vercel config for frontend.
- CORS and auth policy alignment.
- Smoke checks for end-to-end chat + inspector + benchmark run.

## Guardrails to preserve OSS value

- No deploy-vendor lock-in code merged into Core-Memory by default.
- No frontend-framework coupling in Core-Memory runtime modules.
- Keep Core-Memory docs focused on canonical surfaces, with deployment-specific instructions living in `core-memory-demo`.
- Upstream only reusable primitives and bugfixes.

## Deliverables

- `core-memory-demo` repo initialized
- API contract doc + tests
- separate frontend deployed path
- backend deployed path
- parity checklist signed off against baseline features

## Exit criteria

- `Core-Memory` remains clean OSS engine repo without deployment glue bloat.
- `core-memory-demo` provides a production-grade demo path with separate frontend/backend and durable persistence.
