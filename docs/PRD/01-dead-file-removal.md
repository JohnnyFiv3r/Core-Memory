# PRD: Delete Confirmed Dead Files

**Phase:** 1
**Status:** Partially superseded by `docs/compatibility_ledger.md`
**Prerequisite:** Phase 0 complete (CI workflow merged)

---

## Problem

This historical PRD identified three apparent dead files after earlier
refactors. Later cleanup work reclassified the candidates through
`docs/compatibility_ledger.md`: `encryption.py` is public optional compatibility,
`write_ops.py` and `retrieval/pipeline/explain.py` have now been retired after
their separate proof gates passed.

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

## Sub-task 1a — Classify `core_memory/persistence/encryption.py`

> **SUPERSEDED — do not delete.** `encryption.py` is classified in
> `docs/compatibility_ledger.md` as a public optional compatibility module. It is
> not part of the default write path, but callers may import its optional Fernet
> helpers directly. Removal requires a breaking-change process, a replacement
> encryption story, and an active import scan.

**Verify dead** (must produce zero hits outside the file itself and docs):
```bash
grep -rn 'from.*persistence.*import.*encryption\|persistence\.encryption\|persistence/encryption' \
  --include='*.py' --include='*.md' \
  core_memory/ tests/ docs/ benchmarks/ demo/ eval/ scripts/ plugins/ 2>/dev/null
```

**File facts:** 115 lines. Defines `is_encryption_enabled()`, `encrypt_bytes()`,
`decrypt_bytes()`, `encrypt_text()`, `decrypt_text()`, `write_encrypted()`,
`read_encrypted()`, and `generate_key()`. The module imports without the
`[encryption]` extra; cryptography is resolved lazily when helpers need a
configured cipher or generated key.

**Current proving gate:** `tests/test_persistence_encryption_compat.py` covers
no-configuration plaintext behavior, explicit Fernet key round-trips,
passphrase-derived key round-trips, file read/write helpers, and the error raised
when encrypted payloads are read without a configured cipher.

---

## Sub-task 1b — Delete `core_memory/persistence/write_ops.py`

> **Retired.** `write_ops.py` was originally restored as a compatibility shim,
> but the compatibility ledger later classified it as artifact debt. It was
> removed only after the exact import scan returned no active callers outside
> docs and a changelog entry called out the old module path removal.

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
# 1. Retained encryption compatibility is covered, retired files are gone
python -m pytest tests/test_persistence_encryption_compat.py -q
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
- Treat `core_memory/persistence/encryption.py` as a retained public optional
  compatibility module unless a future breaking-change process provides a
  replacement encryption story and a fresh import scan.
- The retired file sub-tasks were handled as separate cleanup slices with their
  own proving gates rather than one broad deletion PR.
- If any `grep` check returns unexpected hits, STOP and document the reference.
  Do not delete a file with live references; instead, file an issue and skip
  that sub-task.
