# PRD: Delete Confirmed Dead Files

**Phase:** 1
**Status:** Partially superseded by `docs/compatibility_ledger.md`
**Prerequisite:** Phase 0 complete (CI workflow merged)

---

## Problem

This historical PRD identified three apparent dead files after earlier
refactors. Later cleanup work reclassified the candidates through
`docs/compatibility_ledger.md`: `encryption.py` is public optional compatibility,
`write_ops.py` remains artifact debt pending a separate proof, and
`retrieval/pipeline/explain.py` has now been retired.

> **Correction from `cleanup-plan.md`:** the cleanup plan originally listed four
> files. `core_memory/retrieval/vector_backend.py` is **NOT dead** — it is
> imported by `core_memory/retrieval/semantic_index.py:from .vector_backend import
> create_vector_backend`. Do not delete it. Update `docs/cleanup-plan.md` to
> remove that bullet (see sub-task 1d).

---

## Success criteria

1. Retired candidates no longer exist in the repo.
2. Retained candidates have an explicit ledger classification and removal
   condition.
3. `pytest tests/ -x -q` passes (same set of tests as before this PR).
4. The `.github/workflows/test.yml` `core-only` and `full` jobs both pass.
5. No new `ImportError` anywhere — verified by a fresh `pip install -e .` then
   `python -c "import core_memory"`.
6. `docs/cleanup-plan.md` is corrected to reflect current retained/retired
   status.

---

## Sub-task 1a — Delete `core_memory/persistence/encryption.py`

> **⚠️ SUPERSEDED — do not delete.** `encryption.py` was restored as a
> backward-compatibility shim in Phase 9f/9g (branch `claude/validate-demo-todos-SCRSz`).
> It now re-exports the canonical symbols so existing callers continue to work.
> Removal requires a breaking-change process and a deprecation cycle. Update this
> sub-task when the shim layer is formally deprecated.

**Verify dead** (must produce zero hits outside the file itself and docs):
```bash
grep -rn 'from.*persistence.*import.*encryption\|persistence\.encryption\|persistence/encryption' \
  --include='*.py' --include='*.md' \
  core_memory/ tests/ docs/ benchmarks/ demo/ eval/ scripts/ plugins/ 2>/dev/null
```

**File facts:** 115 lines. Defines `is_encryption_enabled()`, `encrypt()`,
`decrypt()`, `generate_key()`. Pulls `cryptography` from the `[encryption]` extra.

---

## Sub-task 1b — Delete `core_memory/persistence/write_ops.py`

> **⚠️ SUPERSEDED — do not delete.** `write_ops.py` was restored as a
> backward-compatibility shim in Phase 9f/9g (branch `claude/validate-demo-todos-SCRSz`).
> It re-exports write symbols so callers using the old import path continue to work.
> Removal requires a breaking-change process and a deprecation cycle.

**Verify dead:**
```bash
grep -rn 'from.*persistence.*import.*write_ops\|persistence\.write_ops\|persistence/write_ops' \
  --include='*.py' --include='*.md' \
  core_memory/ tests/ docs/ benchmarks/ demo/ eval/ scripts/ plugins/ 2>/dev/null
```

**File facts:** 31 lines. Thin delegating stub. Re-exports symbols that callers
import directly from elsewhere now.

---

## Sub-task 1c — Delete `core_memory/retrieval/pipeline/explain.py`

> **Retired.** `explain.py` was originally restored as a compatibility shim, but
> the later compatibility ledger classified it as artifact debt. It was removed
> only after the exact import scan returned no active callers outside docs and a
> changelog entry called out the old `build_explain` path removal.

**Verify dead** (note: many files use the word "explain" — this check looks for
imports of *this specific module* or calls to its function):

```bash
grep -rn 'from.*pipeline.*import.*explain\|pipeline\.explain\b\|build_explain' \
  --include='*.py' --include='*.md' \
  core_memory/ tests/ docs/ benchmarks/ demo/ eval/ scripts/ plugins/ 2>/dev/null
```

**File facts:** 25 lines. Defined `build_explain(snapped, snap_decisions, warnings,
retrieval_debug) -> dict`. The active `explain` payload is built inline at
`core_memory/retrieval/pipeline/__init__.py:113-122` — `build_explain` is
unreferenced from first-party code.

**Watch out:** Do not delete or modify `retrieval/pipeline/__init__.py` — its
inline `explain` block is the live implementation.

---

## Sub-task 1d — Correct `docs/cleanup-plan.md`

In the Phase 1 section of `docs/cleanup-plan.md`, remove this bullet:

```markdown
- [ ] `core_memory/retrieval/vector_backend.py` — no imports anywhere
```

Replace the Phase 1 introduction with: "Remove the 3 files with zero references
anywhere in the repo. (The original list of 4 was corrected during Phase 1
investigation — `vector_backend.py` is live, imported by `semantic_index.py`.)"

---

## Verification

```bash
# 1. Files are gone
test ! -e core_memory/persistence/encryption.py
test ! -e core_memory/persistence/write_ops.py
test ! -e core_memory/retrieval/pipeline/explain.py

# 2. Package still imports
pip install -e ".[dev]" -q
python -c "import core_memory; import core_memory.retrieval; import core_memory.persistence; print('ok')"

# 3. Full suite passes (same set as before)
python -m pytest tests/ -x -q --tb=short
```

---

## Guard rails

- **Do not** delete `core_memory/retrieval/vector_backend.py`. It is live code.
- **Do not** edit `retrieval/pipeline/__init__.py` while removing `explain.py` —
  the live `explain` payload code stays.
- Each sub-task is a single file deletion. Open one PR for all four sub-tasks
  (3 deletions + 1 doc fix) — they are tightly coupled and trivial to review
  together.
- If any `grep` check returns unexpected hits, STOP and document the reference.
  Do not delete a file with live references; instead, file an issue and skip
  that sub-task.
