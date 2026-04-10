"""Tests for claim prompt definitions."""

import unittest

from core_memory.claim.prompt_definitions import CLAIM_KIND_DEFINITIONS, CLAIM_UPDATE_DECISION_DEFINITIONS
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS, CLAIM_UPDATE_DECISIONS


class TestClaimPromptDefinitions(unittest.TestCase):
    def test_every_claim_kind_has_definition(self):
        for kind in CANONICAL_CLAIM_KINDS:
            self.assertIn(kind, CLAIM_KIND_DEFINITIONS, f"Missing definition for claim kind: {kind}")

    def test_every_update_decision_has_definition(self):
        for decision in CLAIM_UPDATE_DECISIONS:
            self.assertIn(decision, CLAIM_UPDATE_DECISION_DEFINITIONS, f"Missing definition for decision: {decision}")

    def test_definitions_are_nonempty(self):
        for kind, definition in CLAIM_KIND_DEFINITIONS.items():
            self.assertGreater(len(definition.strip()), 10, f"Definition too short for {kind}")


if __name__ == "__main__":
    unittest.main()
