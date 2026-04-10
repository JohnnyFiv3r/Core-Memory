"""Tests for claim extraction and validation."""

import pytest
from core_memory.claim.extraction import extract_claims, infer_claim_kind
from core_memory.claim.validation import validate_claim, validate_claims_batch, dedup_claims
from core_memory.schema.models import Claim


def test_empty_input_returns_empty():
    assert extract_claims("", "", []) == []


def test_extract_preference_claim():
    claims = extract_claims("I love jazz music", "", [])
    assert len(claims) >= 1
    assert any(c["claim_kind"] == "preference" for c in claims)


def test_extract_policy_claim():
    claims = extract_claims("You should always use markdown", "", [])
    assert any(c["claim_kind"] == "policy" for c in claims)


def test_extract_commitment_claim():
    claims = extract_claims("I will finish the report tomorrow", "", [])
    assert any(c["claim_kind"] == "commitment" for c in claims)


def test_extracted_claims_pass_validation():
    claims = extract_claims("I prefer Python over Java", "", [])
    for c in claims:
        valid, errors = validate_claim(c)
        assert valid, f"Claim failed validation: {errors}"


def test_extracted_claims_match_claim_schema():
    claims = extract_claims("I love coffee in the mornings", "", [])
    for c in claims:
        obj = Claim.from_dict(c)  # should not raise
        assert obj.claim_kind in {"preference", "identity", "policy", "commitment", "condition", "custom"}


def test_dedup_removes_duplicates():
    claims = [
        {"subject": "user", "slot": "preference", "id": "1", "claim_kind": "preference", "value": "x", "reason_text": "r", "confidence": 0.5},
        {"subject": "user", "slot": "preference", "id": "2", "claim_kind": "preference", "value": "y", "reason_text": "r", "confidence": 0.5},
    ]
    result = dedup_claims(claims)
    assert len(result) == 1


def test_validate_missing_field():
    claim = {"id": "1", "claim_kind": "preference", "subject": "user"}
    valid, errors = validate_claim(claim)
    assert not valid
    assert any("slot" in e for e in errors)


def test_validate_empty_reason_text():
    claim = {"id": "1", "claim_kind": "preference", "subject": "user", "slot": "food", "value": "pizza", "reason_text": "", "confidence": 0.8}
    valid, errors = validate_claim(claim)
    assert not valid


def test_validate_confidence_out_of_range():
    claim = {"id": "1", "claim_kind": "preference", "subject": "user", "slot": "food", "value": "pizza", "reason_text": "stated", "confidence": 1.5}
    valid, errors = validate_claim(claim)
    assert not valid


def test_infer_claim_kind_no_keywords_returns_custom():
    assert infer_claim_kind("the sky is blue") == "custom"
