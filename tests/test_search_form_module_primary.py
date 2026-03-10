import unittest

from core_memory.retrieval.search_form import (
    get_search_form,
    SEARCH_FORM_SCHEMA_VERSION,
    SEARCH_FORM_TOOL_ID,
)


class TestSearchFormModulePrimary(unittest.TestCase):
    def test_primary_schema_contract(self):
        catalog = {"x": ["y"]}
        out = get_search_form(catalog)
        self.assertEqual(SEARCH_FORM_SCHEMA_VERSION, out.get("schema_version"))
        self.assertEqual(SEARCH_FORM_TOOL_ID, out.get("tool"))


if __name__ == "__main__":
    unittest.main()
