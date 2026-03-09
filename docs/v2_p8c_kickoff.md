# V2-P8C Kickoff (Retrieval / Schema Closure)

Status: Active

## Objective
Close retrieval schema ambiguity by pinning canonical schema ownership and explicitly demoting compatibility shims.

## Step plan (5)
1. Schema authority map hardening ✅
2. Retrieval contract normalization ✅
3. Read/write path purity sweep ✅
4. Regression + compatibility invariants
5. Full sweep + P8C closeout

## Step 1 completion notes
- Established explicit retrieval schema authority anchors:
  - `core_memory.retrieval.search_form` owns typed-search form schema identifiers.
  - `core_memory.tools.memory.get_search_form` is canonical runtime surface and now enforces canonical schema version defaults.
- Updated canonical-path docs to encode schema ownership and shim demotion language.
- Kept behavior compatibility intact while reducing schema-drift risk.

## Step 2 completion notes
- Normalized retrieval surface contract metadata for canonical wrappers in `core_memory.tools.memory`.
- `memory.search(...)` now sets default stable contract markers:
  - `schema_version=memory_search_result.v1`
  - `contract=typed_search`
- `memory.execute(...)` now sets default stable contract markers (success + gated-error paths):
  - `schema_version=memory_execute_result.v1`
  - `contract=memory_execute`
- Behavior remains backward compatible (`setdefault` semantics preserve richer downstream payloads).

## Step 3 completion notes
- Completed retrieval entry-path purity sweep for runtime/integration modules.
- Reasserted canonical runtime retrieval surface:
  - `core_memory.tools.memory::{get_search_form,search,execute}`
- Marked compatibility shims as non-canonical runtime entries:
  - `core_memory.tools.memory_search`
  - `core_memory.memory_skill.form`
- Added purity guard test:
  - `tests/test_p8c_retrieval_path_purity.py`
  - fails if non-allowed modules import `core_memory.memory_skill` directly.
