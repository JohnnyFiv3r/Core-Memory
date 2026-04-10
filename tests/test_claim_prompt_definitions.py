"""Tests for claim prompt definitions."""

import pytest
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS, CLAIM_UPDATE_DECISIONS
from core_memory.claim.prompt_definitions import CLAIM_KIND_DEFINITIONS, CLAIM_UPDATE_DECISION_DEFINITIONS


def test_every_claim_kind_has_definition():
    for kind in CANONICAL_CLAIM_KINDS:
        assert kind in CLAIM_KIND_DEFINITIONS, f"Missing definition for claim kind: {kind}"


def test_every_update_decision_has_definition():
    for decision in CLAIM_UPDATE_DECISIONS:
        assert decision in CLAIM_UPDATE_DECISION_DEFINITIONS, f"Missing definition for decision: {decision}"


def test_definitions_are_nonempty():
    for kind, defn in CLAIM_KIND_DEFINITIONS.items():
        assert len(defn.strip()) > 10, f"Definition too short for {kind}"
