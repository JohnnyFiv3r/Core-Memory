import unittest

from core_memory.schema.normalization import (
    canonicalize_association_edge,
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
        self.assertEqual("causes", normalize_relation_type("caused_by"))
        self.assertEqual("causes", normalize_relation_type("Causes"))
        self.assertEqual("leads_to", normalize_relation_type("led_to"))
        self.assertEqual("leads_to", normalize_relation_type("leads_to"))
        self.assertEqual("blocks", normalize_relation_type("blocks"))
        self.assertEqual("blocks", normalize_relation_type("blocked_by"))
        self.assertEqual("blocks", normalize_relation_type("blocked"))
        self.assertEqual("unblocks", normalize_relation_type("unblocked"))
        self.assertEqual("supersedes", normalize_relation_type("superseded_by"))
        self.assertEqual("precedes", normalize_relation_type("follows"))
        self.assertEqual("precedes", normalize_relation_type("followed_by"))
        self.assertEqual("enables", normalize_relation_type("enabled"))
        self.assertEqual("contradicts", normalize_relation_type("conflicts_with"))
        self.assertEqual("supports", normalize_relation_type("reinforces"))
        self.assertEqual("similar_pattern", normalize_relation_type("mirrors"))
        self.assertEqual("similar_pattern", normalize_relation_type("structural_symmetry"))
        self.assertEqual("applies_pattern_of", normalize_relation_type("solves_same_mechanism"))
        self.assertEqual("applies_pattern_of", normalize_relation_type("transferable_lesson"))
        self.assertEqual("contradicts", normalize_relation_type("violates_pattern_of"))
        self.assertEqual("generalizes", normalize_relation_type("specializes"))
        self.assertEqual("blocks_unblocks", normalize_relation_type("blocks->unblocks"))

    def test_inverse_relations_swap_endpoints_at_edge_boundary(self):
        caused = canonicalize_association_edge("effect", "cause", "caused_by")
        self.assertEqual("cause", caused["source_bead"])
        self.assertEqual("effect", caused["target_bead"])
        self.assertEqual("causes", caused["relationship"])
        self.assertTrue(caused["endpoints_swapped"])

        active_cause = canonicalize_association_edge("cause", "effect", "causes")
        self.assertEqual("cause", active_cause["source_bead"])
        self.assertEqual("effect", active_cause["target_bead"])
        self.assertEqual("causes", active_cause["relationship"])
        self.assertFalse(active_cause["endpoints_swapped"])

        edge = canonicalize_association_edge("blocked", "blocker", "blocked_by")
        self.assertEqual("blocker", edge["source_bead"])
        self.assertEqual("blocked", edge["target_bead"])
        self.assertEqual("blocks", edge["relationship"])
        self.assertTrue(edge["endpoints_swapped"])

        temporal = canonicalize_association_edge("newer", "older", "follows")
        self.assertEqual("older", temporal["source_bead"])
        self.assertEqual("newer", temporal["target_bead"])
        self.assertEqual("precedes", temporal["relationship"])

        active_block = canonicalize_association_edge("blocker", "blocked", "blocked")
        self.assertEqual("blocker", active_block["source_bead"])
        self.assertEqual("blocked", active_block["target_bead"])
        self.assertEqual("blocks", active_block["relationship"])
        self.assertFalse(active_block["endpoints_swapped"])

        followed_by = canonicalize_association_edge("older", "newer", "followed_by")
        self.assertEqual("older", followed_by["source_bead"])
        self.assertEqual("newer", followed_by["target_bead"])
        self.assertEqual("precedes", followed_by["relationship"])
        self.assertFalse(followed_by["endpoints_swapped"])

    def test_relation_families_use_shared_taxonomy(self):
        self.assertEqual("causal", relation_family("causes"))
        self.assertEqual("evidence", relation_family("supports"))
        self.assertEqual("influence", relation_family("blocked_by"))
        self.assertEqual("influence", relation_family("blocks"))
        self.assertEqual("conflict", relation_family("contradicts"))
        self.assertEqual("temporal", relation_family("follows"))
        self.assertEqual("temporal", relation_family("precedes"))
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
