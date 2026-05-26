# Store Delegation Audit

**Date:** 2026-05-26
**Script:** `scripts/audit_store_delegation.py --md`
**Phase:** 5 (Flatten Persistence Delegation Chain)

| Function                                         | File                         | Verdict  | Notes |
|--------------------------------------------------|------------------------------|----------|-------|
| promotion_slate_for_store                        | promotion_service.py         | STATEFUL | attr:_candidate_recommendation_rows, attr:_read_json, attr:beads_dir |
| evaluate_candidates_for_store                    | promotion_service.py         | STATEFUL | attr:_candidate_recommendation_rows, attr:_read_json, attr:_write_json, attr:beads_dir |
| decide_promotion_for_store                       | promotion_service.py         | STATEFUL | attr:_adaptive_promotion_threshold, attr:_promotion_score, attr:_read_json, attr:_write_json, attr:beads_dir |
| resolve_goal_candidate_for_store                 | promotion_service.py         | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| decide_promotion_bulk_for_store                  | promotion_service.py         | PARTIAL  | passes store |
| decide_session_promotion_states_for_store        | promotion_service.py         | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| promotion_kpis_for_store                         | promotion_service.py         | STATEFUL | attr:_read_json, attr:beads_dir |
| rebalance_promotions_for_store                   | promotion_service.py         | STATEFUL | attr:_adaptive_promotion_threshold, attr:_promotion_score, attr:_read_json, attr:_write_json, attr:beads_dir |
| add_bead_for_store                               | store_add_bead_ops.py        | STATEFUL | attr:_detect_decision_conflicts, attr:_find_recent_duplicate_bead_id, attr:_generate_bead_id, attr:beads_dir |
| resolve_bead_session_id_for_store                | store_add_helpers.py         | STATEFUL | attr:bead_session_id_mode, attr:root |
| title_tokens_for_store                           | store_add_helpers.py         | STATEFUL | attr:_tokenize |
| detect_decision_conflicts_for_store              | store_add_helpers.py         | PARTIAL  | passes store |
| reinforcement_signals_for_store                  | store_autonomy_ops.py        | STATEFUL | attr:_normalize_links |
| append_autonomy_kpi_for_store                    | store_autonomy_ops.py        | STATEFUL | attr:append_metric |
| compact_for_store                                | store_compaction_ops.py      | STATEFUL | attr:_candidate_promotable, attr:_has_evidence, attr:_read_json, attr:_write_json, attr:beads_dir |
| uncompact_for_store                              | store_compaction_ops.py      | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| myelinate_for_store                              | store_compaction_ops.py      | STATEFUL | attr:_read_json, attr:beads_dir |
| active_constraints_for_store                     | store_constraints.py         | STATEFUL | attr:_read_json, attr:beads_dir, attr:extract_constraints |
| check_plan_constraints_for_store                 | store_constraints.py         | PARTIAL  | passes store |
| init_index_for_store                             | store_dream_bootstrap_ops.py | STATEFUL | attr:_write_json, attr:beads_dir, attr:root |
| dream_for_store                                  | store_dream_bootstrap_ops.py | PARTIAL  | passes store |
| find_failure_signature_matches_for_store         | store_failure_ops.py         | STATEFUL | attr:_read_json, attr:beads_dir |
| preflight_failure_check_for_store                | store_failure_ops.py         | STATEFUL | attr:_read_json, attr:beads_dir |
| read_heads_for_store                             | store_index_heads_ops.py     | STATEFUL | attr:_read_json, attr:beads_dir |
| write_heads_for_store                            | store_index_heads_ops.py     | STATEFUL | attr:_write_json, attr:beads_dir |
| update_index_for_store                           | store_index_heads_ops.py     | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir |
| initialize_store_for_store                       | store_init_ops.py            | STATEFUL | attr:_backend, attr:_init_index, attr:assoc_lookback, attr:assoc_top_k, attr:root |
| close_store_for_store                            | store_lifecycle_ops.py       | STATEFUL | attr:_backend |
| safe_del_for_store                               | store_lifecycle_ops.py       | PARTIAL  | passes store |
| rebuild_index_projection_from_sessions_for_store | store_projection_ops.py      | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| candidate_recommendation_rows_for_store          | store_promotion_ops.py       | STATEFUL | attr:_expand_query_tokens, attr:_tokenize |
| promotion_slate_entry_for_store                  | store_promotion_ops.py       | PARTIAL  | passes store |
| evaluate_candidates_entry_for_store              | store_promotion_ops.py       | PARTIAL  | passes store |
| decide_promotion_entry_for_store                 | store_promotion_ops.py       | PARTIAL  | passes store |
| decide_promotion_bulk_entry_for_store            | store_promotion_ops.py       | PARTIAL  | passes store |
| decide_session_promotion_states_entry_for_store  | store_promotion_ops.py       | PARTIAL  | passes store |
| promotion_kpis_entry_for_store                   | store_promotion_ops.py       | PARTIAL  | passes store |
| rebalance_promotions_entry_for_store             | store_promotion_ops.py       | PARTIAL  | passes store |
| query_for_store                                  | store_query.py               | STATEFUL | attr:_normalize_enum, attr:_read_json, attr:beads_dir, attr:root |
| promote_for_store                                | store_relationship_ops.py    | STATEFUL | attr:_has_evidence, attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| link_for_store                                   | store_relationship_ops.py    | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| recall_for_store                                 | store_relationship_ops.py    | STATEFUL | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root, attr:track_bead_recall |
| rebuild_index_for_store                          | store_relationship_ops.py    | STATEFUL | attr:root |
| stats_for_store                                  | store_relationship_ops.py    | STATEFUL | attr:_read_json, attr:beads_dir |
| retrieve_with_context_for_store                  | store_retrieval_context.py   | STATEFUL | attr:_expand_query_tokens, attr:_is_memory_intent, attr:_read_json, attr:_tokenize |
| capture_turn_for_store                           | store_session_ops.py         | STATEFUL | attr:root, attr:track_turn_processed, attr:turns_dir |
| consolidate_for_store                            | store_session_ops.py         | STATEFUL | attr:add_bead, attr:beads_dir, attr:turns_dir |
| validate_bead_fields_for_store                   | store_validation_helpers.py  | STATEFUL | attr:strict_required_fields |

**Summary:** 0 STATELESS, 36 STATEFUL, 12 PARTIAL out of 48 total

---

## PARTIAL functions — manual review required

| Function | File | Bare name refs | Analysis |
|----------|------|---------------|----------|
| decide_promotion_bulk_for_store | promotion_service.py | 1 | Iterates over `decisions`, calls `decide_promotion_for_store(store, ...)` in a loop — store is threaded through |
| detect_decision_conflicts_for_store | store_add_helpers.py | 2 | Calls two other `*_for_store` helpers passing store each time |
| check_plan_constraints_for_store | store_constraints.py | 1 | Calls `active_constraints_for_store(store)` — store used transitively |
| dream_for_store | store_dream_bootstrap_ops.py | 1 | Calls `init_index_for_store(store, ...)` — store used transitively |
| safe_del_for_store | store_lifecycle_ops.py | 1 | Calls `close_store_for_store(store)` — store used transitively |
| promotion_slate_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `promotion_slate_for_store(store, ...)` |
| evaluate_candidates_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `evaluate_candidates_for_store(store, ...)` |
| decide_promotion_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `decide_promotion_for_store(store, ...)` |
| decide_promotion_bulk_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `decide_promotion_bulk_for_store(store, ...)` |
| decide_session_promotion_states_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `decide_session_promotion_states_for_store(store, ...)` |
| promotion_kpis_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `promotion_kpis_for_store(store, ...)` |
| rebalance_promotions_entry_for_store | store_promotion_ops.py | 1 | Pure pass-through to `rebalance_promotions_for_store(store, ...)` |

### Key finding: `store_promotion_ops.py` is a redundant indirection layer

Seven of the 12 PARTIAL functions are in `store_promotion_ops.py`. Each is a one-liner that
forwards to the identically-named function in `promotion_service.py`. The mixin
(`StoreReportingPromotionMixin`) calls these wrappers with `self`. The net effect is:

```
MemoryStore.promotion_slate(...)
  → StoreReportingPromotionMixin.promotion_slate(self, ...)
    → store_promotion_ops.promotion_slate_entry_for_store(store, ...)   # ← pure pass-through
      → promotion_service.promotion_slate_for_store(store, ...)         # ← real work
```

This is a 4-hop chain where hop 3 does nothing. Step 5c for `store_promotion_ops.py`
should inline the mixin to call `promotion_service` directly, eliminating the `*_entry_*`
wrappers entirely.

---

## Findings summary

- **0 STATELESS** functions — no `*_for_store` function fully ignores its `store` argument.
  The PRD pilot (`store_text_hygiene_ops.py`) is already complete: those functions no longer
  carry a `store` parameter at all, so the audit script does not see them.

- **36 STATEFUL** functions — genuine use of `store` attributes (`store._read_json`,
  `store.beads_dir`, etc.). These cannot be simplified without changing what the operations do.

- **12 PARTIAL** functions — `store` is passed through to another function but never accessed
  directly. These are candidates for elimination via call-site inlining (Step 5c).

---

## Recommended Step 5c order

Based on this audit, apply the following order (simplest first):

1. `store_promotion_ops.py` — 7 pure pass-throughs; mixin can call `promotion_service` directly
2. `store_lifecycle_ops.py` — `safe_del_for_store` is a single-function file with 1 PARTIAL
3. `store_dream_bootstrap_ops.py` — `dream_for_store` is 1 PARTIAL, 1 STATEFUL
4. `store_constraints.py` — `check_plan_constraints_for_store` is 1 PARTIAL, 1 STATEFUL
5. `store_add_helpers.py` — `detect_decision_conflicts_for_store` is 1 PARTIAL, 2 STATEFUL
6. `promotion_service.py` — `decide_promotion_bulk_for_store` loops over `decide_promotion_for_store`; handle last
