# Store Delegation Audit

| Function                                         | File                         | Verdict   | Notes |
|--------------------------------------------------|------------------------------|-----------|-------|
| promotion_slate_for_store                        | promotion_service.py         | STATEFUL  | attr:_candidate_recommendation_rows, attr:_read_json, attr:beads_dir |
| evaluate_candidates_for_store                    | promotion_service.py         | STATEFUL  | attr:_candidate_recommendation_rows, attr:_read_json, attr:_write_json, attr:bea |
| decide_promotion_for_store                       | promotion_service.py         | STATEFUL  | attr:_adaptive_promotion_threshold, attr:_promotion_score, attr:_read_json, attr |
| resolve_goal_candidate_for_store                 | promotion_service.py         | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| decide_promotion_bulk_for_store                  | promotion_service.py         | PARTIAL   | passes store |
| decide_session_promotion_states_for_store        | promotion_service.py         | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| promotion_kpis_for_store                         | promotion_service.py         | STATEFUL  | attr:_read_json, attr:beads_dir |
| rebalance_promotions_for_store                   | promotion_service.py         | STATEFUL  | attr:_adaptive_promotion_threshold, attr:_promotion_score, attr:_read_json, attr |
| add_bead_for_store                               | store_add_bead_ops.py        | STATEFUL  | attr:_detect_decision_conflicts, attr:_find_recent_duplicate_bead_id, attr:_gene |
| resolve_bead_session_id_for_store                | store_add_helpers.py         | STATEFUL  | attr:bead_session_id_mode, attr:root |
| title_tokens_for_store                           | store_add_helpers.py         | STATEFUL  | attr:_tokenize |
| detect_decision_conflicts_for_store              | store_add_helpers.py         | PARTIAL   | passes store |
| find_recent_duplicate_bead_id_for_store          | store_add_helpers.py         | STATELESS |  |
| reinforcement_signals_for_store                  | store_autonomy_ops.py        | STATEFUL  | attr:_normalize_links |
| append_autonomy_kpi_for_store                    | store_autonomy_ops.py        | STATEFUL  | attr:append_metric |
| compact_for_store                                | store_compaction_ops.py      | STATEFUL  | attr:_candidate_promotable, attr:_has_evidence, attr:_read_json, attr:_write_jso |
| uncompact_for_store                              | store_compaction_ops.py      | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| myelinate_for_store                              | store_compaction_ops.py      | STATEFUL  | attr:_read_json, attr:beads_dir |
| active_constraints_for_store                     | store_constraints.py         | STATEFUL  | attr:_read_json, attr:beads_dir, attr:extract_constraints |
| check_plan_constraints_for_store                 | store_constraints.py         | PARTIAL   | passes store |
| init_index_for_store                             | store_dream_bootstrap_ops.py | STATEFUL  | attr:_write_json, attr:beads_dir, attr:root |
| dream_for_store                                  | store_dream_bootstrap_ops.py | PARTIAL   | passes store |
| compute_failure_signature_for_store              | store_failure_ops.py         | STATELESS |  |
| find_failure_signature_matches_for_store         | store_failure_ops.py         | STATEFUL  | attr:_read_json, attr:beads_dir |
| preflight_failure_check_for_store                | store_failure_ops.py         | STATEFUL  | attr:_read_json, attr:beads_dir |
| read_heads_for_store                             | store_index_heads_ops.py     | STATEFUL  | attr:_read_json, attr:beads_dir |
| write_heads_for_store                            | store_index_heads_ops.py     | STATEFUL  | attr:_write_json, attr:beads_dir |
| update_heads_for_bead_for_store                  | store_index_heads_ops.py     | STATELESS |  |
| update_index_for_store                           | store_index_heads_ops.py     | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir |
| initialize_store_for_store                       | store_init_ops.py            | STATEFUL  | attr:_backend, attr:_init_index, attr:assoc_lookback, attr:assoc_top_k, attr:ass |
| close_store_for_store                            | store_lifecycle_ops.py       | STATEFUL  | attr:_backend |
| safe_del_for_store                               | store_lifecycle_ops.py       | PARTIAL   | passes store |
| rebuild_index_projection_from_sessions_for_store | store_projection_ops.py      | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| promotion_score_for_store                        | store_promotion_ops.py       | STATELESS |  |
| adaptive_promotion_threshold_for_store           | store_promotion_ops.py       | STATELESS |  |
| candidate_promotable_for_store                   | store_promotion_ops.py       | STATELESS |  |
| candidate_recommendation_rows_for_store          | store_promotion_ops.py       | STATEFUL  | attr:_expand_query_tokens, attr:_tokenize |
| promotion_slate_entry_for_store                  | store_promotion_ops.py       | PARTIAL   | passes store |
| evaluate_candidates_entry_for_store              | store_promotion_ops.py       | PARTIAL   | passes store |
| decide_promotion_entry_for_store                 | store_promotion_ops.py       | PARTIAL   | passes store |
| decide_promotion_bulk_entry_for_store            | store_promotion_ops.py       | PARTIAL   | passes store |
| decide_session_promotion_states_entry_for_store  | store_promotion_ops.py       | PARTIAL   | passes store |
| promotion_kpis_entry_for_store                   | store_promotion_ops.py       | PARTIAL   | passes store |
| rebalance_promotions_entry_for_store             | store_promotion_ops.py       | PARTIAL   | passes store |
| query_for_store                                  | store_query.py               | STATEFUL  | attr:_normalize_enum, attr:_read_json, attr:beads_dir, attr:root |
| promote_for_store                                | store_relationship_ops.py    | STATEFUL  | attr:_has_evidence, attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| link_for_store                                   | store_relationship_ops.py    | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root |
| recall_for_store                                 | store_relationship_ops.py    | STATEFUL  | attr:_read_json, attr:_write_json, attr:beads_dir, attr:root, attr:track_bead_re |
| rebuild_index_for_store                          | store_relationship_ops.py    | STATEFUL  | attr:root |
| stats_for_store                                  | store_relationship_ops.py    | STATEFUL  | attr:_read_json, attr:beads_dir |
| retrieve_with_context_for_store                  | store_retrieval_context.py   | STATEFUL  | attr:_expand_query_tokens, attr:_is_memory_intent, attr:_read_json, attr:_tokeni |
| capture_turn_for_store                           | store_session_ops.py         | STATEFUL  | attr:root, attr:track_turn_processed, attr:turns_dir |
| consolidate_for_store                            | store_session_ops.py         | STATEFUL  | attr:add_bead, attr:beads_dir, attr:turns_dir |
| tokenize_for_store                               | store_text_hygiene_ops.py    | STATELESS |  |
| is_memory_intent_for_store                       | store_text_hygiene_ops.py    | STATELESS |  |
| expand_query_tokens_for_store                    | store_text_hygiene_ops.py    | STATELESS |  |
| redact_text_for_store                            | store_text_hygiene_ops.py    | STATELESS |  |
| sanitize_bead_content_for_store                  | store_text_hygiene_ops.py    | STATELESS |  |
| extract_constraints_for_store                    | store_text_hygiene_ops.py    | STATELESS |  |
| required_field_issues_for_store                  | store_validation_helpers.py  | STATELESS |  |
| validate_bead_fields_for_store                   | store_validation_helpers.py  | STATEFUL  | attr:strict_required_fields |

**Summary:** 13 STATELESS, 36 STATEFUL, 12 PARTIAL out of 61 total


STATELESS (safe to remove store param):
  store_add_helpers.py::find_recent_duplicate_bead_id_for_store
  store_failure_ops.py::compute_failure_signature_for_store
  store_index_heads_ops.py::update_heads_for_bead_for_store
  store_promotion_ops.py::promotion_score_for_store
  store_promotion_ops.py::adaptive_promotion_threshold_for_store
  store_promotion_ops.py::candidate_promotable_for_store
  store_text_hygiene_ops.py::tokenize_for_store
  store_text_hygiene_ops.py::is_memory_intent_for_store
  store_text_hygiene_ops.py::expand_query_tokens_for_store
  store_text_hygiene_ops.py::redact_text_for_store
  store_text_hygiene_ops.py::sanitize_bead_content_for_store
  store_text_hygiene_ops.py::extract_constraints_for_store
  store_validation_helpers.py::required_field_issues_for_store

PARTIAL (review manually):
  promotion_service.py::decide_promotion_bulk_for_store  [1 bare name refs]
  store_add_helpers.py::detect_decision_conflicts_for_store  [2 bare name refs]
  store_constraints.py::check_plan_constraints_for_store  [1 bare name refs]
  store_dream_bootstrap_ops.py::dream_for_store  [1 bare name refs]
  store_lifecycle_ops.py::safe_del_for_store  [1 bare name refs]
  store_promotion_ops.py::promotion_slate_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::evaluate_candidates_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::decide_promotion_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::decide_promotion_bulk_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::decide_session_promotion_states_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::promotion_kpis_entry_for_store  [1 bare name refs]
  store_promotion_ops.py::rebalance_promotions_entry_for_store  [1 bare name refs]
