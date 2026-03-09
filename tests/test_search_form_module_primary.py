import unittest

from core_memory.retrieval.search_form import get_search_form as primary_get_form
from core_memory.memory_skill.form import get_search_form as shim_get_form


class TestSearchFormModulePrimary(unittest.TestCase):
    def test_primary_and_shim_match(self):
        catalog = {"x": ["y"]}
        a = primary_get_form(catalog)
        b = shim_get_form(catalog)
        self.assertEqual(a, b)
        self.assertEqual("memory_search_form.v1", a.get("schema_version"))


if __name__ == "__main__":
    unittest.main()
