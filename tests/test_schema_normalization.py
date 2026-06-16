import unittest

from core_memory.schema.normalization import (
    normalize_bead_type,
    is_allowed_bead_type,
    is_causal_relation,
    is_evidential_relation,
    normalize_relation_type,
    relation_family,
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

    def test_relation_aliases_normalize_without_direction_rewrite(self):
        self.assertEqual("caused_by", normalize_relation_type("Causes"))
        self.assertEqual("led_to", normalize_relation_type("leads_to"))
        self.assertEqual("blocks", normalize_relation_type("blocks"))
        self.assertEqual("blocked_by", normalize_relation_type("blocked"))
        self.assertEqual("unblocks", normalize_relation_type("unblocked"))
        self.assertEqual("enables", normalize_relation_type("enabled"))
        self.assertEqual("contradicts", normalize_relation_type("conflicts_with"))
        self.assertEqual("blocks_unblocks", normalize_relation_type("blocks->unblocks"))

    def test_relation_families_use_shared_taxonomy(self):
        self.assertEqual("causal", relation_family("causes"))
        self.assertEqual("evidence", relation_family("supports"))
        self.assertEqual("influence", relation_family("blocked_by"))
        self.assertEqual("conflict", relation_family("contradicts"))
        self.assertEqual("temporal", relation_family("follows"))
        self.assertEqual("revision", relation_family("supersedes"))
        self.assertEqual("derived", relation_family("shared_tag"))
        self.assertEqual("evidence", relation_family("documented_by"))
        self.assertEqual("related", relation_family("not-a-relation"))
        self.assertTrue(is_evidential_relation("leads_to"))
        self.assertFalse(is_evidential_relation("documented_by"))
        self.assertTrue(is_causal_relation("Causes"))
        self.assertFalse(is_causal_relation("supports"))


if __name__ == "__main__":
    unittest.main()
