import unittest

from core_memory.schema import (
    normalize_bead_type,
    is_allowed_bead_type,
    normalize_relation_type,
    relation_kind,
)


class TestSchemaNormalization(unittest.TestCase):
    def test_legacy_bead_aliases_normalize(self):
        self.assertEqual("lesson", normalize_bead_type("promoted_lesson"))
        self.assertEqual("decision", normalize_bead_type("promoted_decision"))

    def test_allowed_bead_types(self):
        self.assertTrue(is_allowed_bead_type("context"))
        self.assertTrue(is_allowed_bead_type("promoted_lesson"))
        self.assertFalse(is_allowed_bead_type("not_a_real_type"))

    def test_relation_kind_split(self):
        self.assertEqual("canonical", relation_kind("supports"))
        self.assertEqual("derived", relation_kind("shared_tag"))
        self.assertEqual("unknown", relation_kind("something_else"))
        self.assertEqual("associated_with", normalize_relation_type(None))


if __name__ == "__main__":
    unittest.main()
