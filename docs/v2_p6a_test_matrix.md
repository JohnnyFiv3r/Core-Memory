# V2-P6A Test Matrix

Status: Draft for execution

## A) Session-first authority
1. `test_live_session_reads_from_session_surface_first`
2. `test_index_projection_does_not_override_live_session_truth`
3. `test_flush_commits_session_surface_to_archive_without_live_loss`

## B) Memory engine ownership
4. `test_runtime_entrypoints_route_through_memory_engine`
5. `test_engine_owns_trigger_orchestration_order`

## C) Retrieval catalog relation sourcing
6. `test_catalog_relations_from_canonical_association_records`
7. `test_catalog_no_longer_depends_on_bead_local_links_for_relation_types`

## D) Rolling surface ownership
8. `test_rolling_surface_module_is_authoritative_for_continuity_projection`
9. `test_rolling_surface_metadata_and_storage_contract_stable`

## E) System safety
10. `test_no_contract_drift_execute_search_reason`
11. `test_full_e2e_turn_flush_query_path_stable`

## Exit threshold
- P6A matrix all green
- full regression green
- eval stability maintained
