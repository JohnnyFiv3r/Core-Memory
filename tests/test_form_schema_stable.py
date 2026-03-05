import tempfile
import unittest

from core_memory.memory_skill import memory_get_search_form


class TestFormSchemaStable(unittest.TestCase):
    def test_schema_has_required_fields(self):
        with tempfile.TemporaryDirectory() as td:
            f = memory_get_search_form(td)
            self.assertEqual("memory_search_form.v1", f.get("schema_version"))
            fields = f.get("fields") or {}
            for k in ["intent", "query_text", "incident_id", "topic_keys", "bead_types", "relation_types", "k", "require_structural"]:
                self.assertIn(k, fields)


if __name__ == "__main__":
    unittest.main()
