# V2-P8C Kickoff (Retrieval / Schema Closure)

Status: Active

## Objective
Close retrieval schema ambiguity by pinning canonical schema ownership and explicitly demoting compatibility shims.

## Step plan (5)
1. Schema authority map hardening ✅
2. Retrieval contract normalization
3. Read/write path purity sweep
4. Regression + compatibility invariants
5. Full sweep + P8C closeout

## Step 1 completion notes
- Established explicit retrieval schema authority anchors:
  - `core_memory.retrieval.search_form` owns typed-search form schema identifiers.
  - `core_memory.tools.memory.get_search_form` is canonical runtime surface and now enforces canonical schema version defaults.
- Updated canonical-path docs to encode schema ownership and shim demotion language.
- Kept behavior compatibility intact while reducing schema-drift risk.
