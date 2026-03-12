# Full Sweep Triage — 2026-03-12

Command:
- `python3 -m unittest discover -s tests -p 'test_*.py'`

Result:
- Ran: 235
- Failures: 1
- Errors: 9
- Skipped: 7

## Red item triage

### A) Sidecar sync import path break (6 errors)
- Tests:
  - `test_p9_session_purity_invariants` (3 errors)
  - `test_sidecar_sync_session_semantics` (3 errors)
- Error:
  - `ModuleNotFoundError: No module named 'core_memory.sidecar_worker'`
- Location:
  - `scripts/sidecar_sync_session.py` imports `core_memory.sidecar_worker.SidecarPolicy`
- Triage:
  - High-confidence code drift. Canonical worker module appears to be `core_memory.event_worker`.
- Proposed fix:
  - Update import in `scripts/sidecar_sync_session.py` to `from core_memory.event_worker import SidecarPolicy`.
  - Add compatibility shim only if required by historical callers.

### B) Missing kickoff doc artifact (1 error)
- Test:
  - `test_p9_session_purity_invariants.test_kickoff_doc_marks_step_progress`
- Error:
  - Missing file `docs/v2_p9_kickoff.md`
- Triage:
  - Expected artifact absent from branch.
- Proposed fix:
  - Add the expected doc with required step-progress markers (per test assertions).

### C) Optional dependency not installed in test env (1 error)
- Test:
  - `test_springai_bridge.test_get_app_returns_fastapi`
- Error:
  - `ModuleNotFoundError: No module named 'fastapi'`
- Triage:
  - Environment/dependency issue unless project intends test to skip without FastAPI.
- Proposed fix options:
  1) install `fastapi` in test environment, or
  2) make test conditional/skip when FastAPI missing, or
  3) lazy-import fallback path in bridge.

### D) Admin flush script path mismatch (1 error)
- Test:
  - `test_v2_p2_enforcement_matrix.test_admin_flush_cli_uses_canonical_path`
- Error:
  - subprocess path `/home/node/.openclaw/workspace/scripts/consolidate.py` not found.
- Triage:
  - Test assumes script under workspace root; script exists under repo (`Core-Memory/scripts/...`) context mismatch.
- Proposed fix:
  - Either normalize test to repo-relative script path, or provide stable wrapper path expected by test.

### E) Store API contract mismatch (1 error + 1 failure)
- Tests:
  - `test_v2p21_store_integrity.test_failure_signature_ranking_prefers_overlap_then_recency`
  - `test_v2p21_store_integrity.test_add_bead_rejects_reserved_overrides`
- Errors:
  - `find_failure_signature_matches()` no longer accepts `tags=` kwarg (expects `plan`, `context_tags`).
  - `add_bead` does not raise `ValueError` for reserved overrides expected by test.
- Triage:
  - API/behavior drift against test contract.
- Proposed fix:
  - Add backward-compat adapter for `tags` input, or update tests + migration notes deliberately.
  - Reinstate reserved override validation in `add_bead` (or codify changed policy and update tests/docs together).

---

## Uncommitted items check

### 1) Modified
- `plugins/openclaw-core-memory-bridge/index.js`
- Diff summary:
  - Added robust child-process handling (`spawn error`, `stdin error`, guarded `stdin.end`) with single-settle promise logic.
- Assessment:
  - Valuable stability fix tied to observed `EPIPE` crash loop during Telegram tests.
- Recommendation:
  - Commit this patch (or revert intentionally and capture rationale).

### 2) Untracked
- `docs/reports/write-side-contract-implementation-plan-2026-03-12.md`
- Assessment:
  - Useful design artifact referenced during planning.
- Recommendation:
  - Commit as documentation artifact for traceability.

---

## Suggested next execution order
1. Fix sidecar import drift (`sidecar_worker` -> `event_worker`) + rerun affected 6 tests.
2. Add/restore `docs/v2_p9_kickoff.md` expected markers.
3. Resolve `scripts/consolidate.py` path expectation in enforcement matrix test.
4. Restore store API compatibility (`find_failure_signature_matches(tags=...)` and reserved override behavior) or update tests with migration note.
5. Decide FastAPI strategy for test env (install vs skip policy).
6. Commit uncommitted plugin + plan doc once approved.
