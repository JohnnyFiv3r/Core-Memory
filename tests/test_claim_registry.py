import pytest
from core_memory.claim.registry import (
    get_all_definitions,
    CLAIM_KIND_DEFINITIONS,
    CLAIM_UPDATE_DECISION_DEFINITIONS,
    MEMORY_INTERACTION_ROLE_DEFINITIONS,
    RETRIEVAL_MODE_DEFINITIONS,
    ANSWER_OUTCOME_DEFINITIONS,
    LIFECYCLE_STATUS_DEFINITIONS,
    PROMOTION_STATE_DEFINITIONS,
)
from core_memory.schema.normalization import CANONICAL_CLAIM_KINDS, CLAIM_UPDATE_DECISIONS


def test_get_all_definitions_returns_all_categories():
    defs = get_all_definitions()
    expected_keys = {
        "bead_types", "claim_kinds", "claim_update_decisions",
        "memory_interaction_roles", "retrieval_modes", "answer_outcomes",
        "lifecycle_statuses", "promotion_states", "relation_labels",
    }
    assert expected_keys.issubset(set(defs.keys()))


def test_every_claim_kind_has_definition():
    for kind in CANONICAL_CLAIM_KINDS:
        assert kind in CLAIM_KIND_DEFINITIONS, f"No definition for claim kind: {kind}"


def test_every_update_decision_has_definition():
    for decision in CLAIM_UPDATE_DECISIONS:
        assert decision in CLAIM_UPDATE_DECISION_DEFINITIONS, f"No definition for decision: {decision}"


def test_interaction_roles_defined():
    for role in ["memory_resolution", "memory_correction", "memory_reflection"]:
        assert role in MEMORY_INTERACTION_ROLE_DEFINITIONS


def test_retrieval_modes_defined():
    for mode in ["fact_first", "causal_first", "temporal_first", "mixed"]:
        assert mode in RETRIEVAL_MODE_DEFINITIONS


def test_answer_outcomes_defined():
    for outcome in ["answer_current", "answer_historical", "answer_partial", "abstain"]:
        assert outcome in ANSWER_OUTCOME_DEFINITIONS


def test_lifecycle_statuses_defined():
    for status in ["default", "archived", "superseded"]:
        assert status in LIFECYCLE_STATUS_DEFINITIONS


def test_promotion_states_defined():
    for state in ["null", "candidate", "promoted"]:
        assert state in PROMOTION_STATE_DEFINITIONS


def test_definitions_are_nonempty_strings():
    defs = get_all_definitions()
    for category, label_defs in defs.items():
        for label, defn in label_defs.items():
            assert isinstance(defn, str), f"{category}.{label} is not a string"
            assert len(defn.strip()) > 0, f"{category}.{label} has empty definition"
