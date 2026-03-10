#!/usr/bin/env bash
set -euo pipefail

python3 -m unittest \
  tests.test_live_session_authority \
  tests.test_memory_engine \
  tests.test_association_crawler_contract \
  tests.test_rolling_surface_contract \
  tests.test_rolling_surface_owner \
  tests.test_rolling_surface_separation \
  tests.test_search_form_module_primary \
  tests.test_catalog_relation_source \
  tests.test_memory_search_tool_wrapper \
  tests.test_memory_execute_contract \
  tests.test_openclaw_agent_end_bridge \
  tests.test_adapter_contract_markers \
  tests.test_pydanticai_adapter \
  tests.test_pre_oss_matrix -v
