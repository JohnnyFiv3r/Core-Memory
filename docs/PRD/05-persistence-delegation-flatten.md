# PRD: Flatten Persistence Delegation Chain

**Phase:** 5
**Status:** Complete at architecture layer — MRO flat; legacy mixin artifacts retired
**Prerequisite:** Phase 4 complete

---

## Current implementation note

The Phase 5 cleanup is complete at the architecture layer. `MemoryStore` no
longer inherits the legacy persistence mixin assemblers; its public methods live
directly on `core_memory.persistence.store.MemoryStore`, preserving the public
API while removing the MRO hop. The legacy files
`core_memory/persistence/store_core_delegates_mixin.py` and
`core_memory/persistence/store_reporting_promotion_mixin.py` have been retired.

Some `store_*_ops.py` helper modules and `*_for_store` functions remain as
implementation helpers. That is no longer cleanup debt by itself: the completed
Phase 5 boundary was the public `MemoryStore` assembly flattening, not a mandate
to delete every helper or remove every `store` parameter. Public behavior is
covered by `tests/test_memory_store_public_boundary_contract.py` and related
`mixin_assembly` tests.

The plan below is retained as historical rationale for the cleanup.

---

## Problem

At the time this PRD was written, `MemoryStore` inherited 82 methods through two
mixins (`StoreCoreDelegatesMixin`, `StoreReportingPromotionMixin`). Each mixin
method forwarded a call to a `*_for_store` function in a `store_*_ops.py` file.
Those functions accepted `store: Any` as their first argument but, in many cases,
did not use it — they called into `policy/`, `retrieval/`, or `persistence/`
modules that took no store reference.

The result was a 3-hop call chain for many store operations:

```
MemoryStore.method(args)
  → mixin.method(self, args)           # hop 1: method on MemoryStore via MRO
    → ops_file.thing_for_store(store, args)   # hop 2: free function that ignores store
      → policy.real_logic(args)        # hop 3: actual work
```

This hid where logic actually lived, made the store surface look stateful when it
was mostly not, and made the codebase harder to read and test in isolation.

---

## Current implementation state

| Category | File count | Examples |
|----------|-----------|---------|
| Public store assembly | Flat | `MemoryStore` defines public methods directly in `store.py` |
| Retired mixin assemblers | 0 active | `store_core_delegates_mixin.py`, `store_reporting_promotion_mixin.py` retired |
| Helper operation modules | Retained | `store_add_bead_ops.py`, `store_text_hygiene_ops.py`, `store_promotion_ops.py`, etc. |
| Data/contract types | Retained | `store_contract.py`, `store_constraints.py`, `store_validation_helpers.py` |

Helper operation modules remain allowed when they keep `MemoryStore` readable and
preserve public behavior. Future helper cleanup should be justified by a concrete
boundary or duplication reduction, not by Phase 5 status.

---

## Success criteria / outcome

1. Legacy mixin artifacts are retired.
2. `MemoryStore` has a flat public assembly with no persistence mixin MRO hop.
3. Public `MemoryStore` method names and signatures are preserved.
4. Helper operation modules remain internal implementation details.
5. Public boundary and side-effect behavior are covered by focused store tests.
6. No public `MemoryStore` API is removed or renamed.

---

## Historical scope

**In:**
- Audit script classifying each `*_for_store` function
- Removal of unused `store` parameters from STATELESS functions
- Mixin method simplification (call underlying function directly, no `self` pass-through)
- Optional: inline thin mixins into `MemoryStore` after ops files are flattened

**Out:**
- Any change to what the operations actually do
- Removing mixin classes entirely (that is a separate architectural decision)
- Changes to `store_add_bead_ops.py` or other real-implementation files

---

## Historical implementation order

### Step 5a — Audit script

Write `scripts/audit_store_delegation.py`. For each `*_for_store` function:
1. Parse the AST.
2. Check if `store` appears anywhere in the function body (attribute access, passed to
   another function, etc.).
3. Output a table: `function_name | file | verdict (STATEFUL/STATELESS/PARTIAL)`.

Run the script and commit its output as `docs/reports/store-delegation-audit-<date>.md`.
This is the source of truth for Steps 5b–5c.

### Step 5b — Pilot: `store_text_hygiene_ops.py`

The audit identified `tokenize_for_store(store, text)` as a clear STATELESS function that
ignores `store` and calls `query_norm._tokenize(text)` directly. Use this as the pilot:

1. Remove `store` parameter from all STATELESS functions in the file.
2. Update `StoreCoreDelegatesMixin` method for each affected function — call the function
   without `self`.
3. Run `pytest -m mixin_assembly` + full suite.
4. If green, commit as a standalone PR.

### Step 5c — Remaining delegation files

Apply the same recipe to each remaining file, in order of ascending complexity. One PR per
file. Never batch two files in one commit.

Order (revise based on actual audit output):
1. `store_compaction_ops.py`
2. `store_dream_bootstrap_ops.py`
3. `store_index_heads_ops.py`
4. `store_session_ops.py`
5. `store_autonomy_ops.py`
6. `store_failure_ops.py`
7. `store_lifecycle_ops.py`
8. `store_promotion_ops.py`
9. `store_relationship_ops.py`

For PARTIAL functions (use `store` conditionally): handle case-by-case. Options are:
- Extract the stateful part into the mixin method body
- Pass only the needed field (e.g., `store.root`) instead of the whole store
- Leave as-is and document why

### Step 5d — Mixin consolidation (optional, post 5c)

After all 10 delegation files are flat, evaluate whether the two mixins are still carrying
their weight or whether `MemoryStore` can inherit directly from a single thin mixin or no
mixin at all. This is an architectural decision gated on seeing the real method count after
5a–5c.

Do not attempt 5d speculatively. The code will be clearer after 5c than it is now.

---

## Guard rails

- **Do not change the public `MemoryStore` method signatures.** Callers use `store.promote_bead(id)` etc. — those stay.
- **Do not merge Phase 5 PRs during an active release cut.** Each step should be on its
  own branch; merge sequentially into the cleanup branch, not directly to main.
- **The audit script is authoritative.** If a function is PARTIAL and you are not sure how
  to handle it, stop and document the finding rather than guessing.
