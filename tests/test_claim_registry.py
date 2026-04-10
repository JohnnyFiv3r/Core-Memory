import unittest

from core_memory.claim.registry import (
    ANSWER_OUTCOME_DEFINITIONS,
    CLAIM_KIND_DEFINITIONS,
    CLAIM_UPDATE_DECISION_DEFINITIONS,
    LIFECYCLE_STATUS_DEFINITIONS,
    MEMORY_INTERACTION_ROLE_DEFINITIONS,
    PROMOTION_STATE_DEFINITIONS,
    RETRIEVAL_MODE_DEFINITIONS,
    get_all_definitions,
)
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS, CLAIM_UPDATE_DECISIONS


class TestClaimRegistry(unittest.TestCase):
    def test_get_all_definitions_returns_all_categories(self):
        defs = get_all_definitions()
        expected = {
            "bead_types",
            "claim_kinds",
            "claim_update_decisions",
            "memory_interaction_roles",
            "retrieval_modes",
            "answer_outcomes",
            "lifecycle_statuses",
            "promotion_states",
            "relation_labels",
        }
        self.assertTrue(expected.issubset(set(defs.keys())))

    def test_every_claim_kind_has_definition(self):
        for kind in CANONICAL_CLAIM_KINDS:
            self.assertIn(kind, CLAIM_KIND_DEFINITIONS)

    def test_every_update_decision_has_definition(self):
        for decision in CLAIM_UPDATE_DECISIONS:
            self.assertIn(decision, CLAIM_UPDATE_DECISION_DEFINITIONS)

    def test_interaction_roles_defined(self):
        for role in ["memory_resolution", "memory_correction", "memory_reflection"]:
            self.assertIn(role, MEMORY_INTERACTION_ROLE_DEFINITIONS)

    def test_retrieval_modes_defined(self):
        for mode in ["fact_first", "causal_first", "temporal_first", "mixed"]:
            self.assertIn(mode, RETRIEVAL_MODE_DEFINITIONS)

    def test_answer_outcomes_defined(self):
        for outcome in ["answer_current", "answer_historical", "answer_partial", "abstain"]:
            self.assertIn(outcome, ANSWER_OUTCOME_DEFINITIONS)

    def test_lifecycle_statuses_defined(self):
        for status in ["default", "archived", "superseded"]:
            self.assertIn(status, LIFECYCLE_STATUS_DEFINITIONS)

    def test_promotion_states_defined(self):
        for state in ["null", "candidate", "promoted"]:
            self.assertIn(state, PROMOTION_STATE_DEFINITIONS)

    def test_definitions_are_nonempty_strings(self):
        defs = get_all_definitions()
        for category, label_defs in defs.items():
            for label, definition in label_defs.items():
                self.assertIsInstance(definition, str, f"{category}.{label} is not a string")
                self.assertTrue(definition.strip(), f"{category}.{label} has empty definition")


if __name__ == "__main__":
    unittest.main()
