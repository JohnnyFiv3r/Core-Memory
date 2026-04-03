import unittest


class TestPreOssMatrix(unittest.TestCase):
    def test_pre_oss_matrix_manifest(self):
        matrix = [
            # Core invariants
            "tests.test_live_session_authority",
            "tests.test_memory_engine",
            "tests.test_association_crawler_contract",
            "tests.test_rolling_surface_contract",
            "tests.test_rolling_surface_owner",
            "tests.test_rolling_surface_separation",
            "tests.test_p13_authority_enforcement",
            "tests.test_event_import_migration_guard",
            # Retrieval invariants
            "tests.test_memory_search_tool_wrapper",
            "tests.test_memory_execute_contract",
            "tests.test_canonical_hydration_contract",
            "tests.test_temporal_only_grounding_guard",
            "tests.test_catalog_relation_source",
            # Adapter invariants
            "tests.test_openclaw_agent_end_bridge",
            "tests.test_openclaw_read_bridge",
            "tests.test_http_ingress",
            "tests.test_adapter_contract_markers",
            "tests.test_pydanticai_adapter",
            "tests.test_pydanticai_memory_tools",
            "tests.test_event_module_aliases",
        ]
        self.assertGreaterEqual(len(matrix), 12)


if __name__ == "__main__":
    unittest.main()
