"""Tests for claim extraction and validation."""

import unittest

from core_memory.claim.extraction import extract_claims, infer_claim_kind
from core_memory.claim.validation import dedup_claims, validate_claim
from core_memory.schema.models import Claim


class TestClaimExtraction(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual([], extract_claims("", "", []))

    def test_extract_preference_claim(self):
        claims = extract_claims("I love jazz music", "", [])
        self.assertGreaterEqual(len(claims), 1)
        self.assertTrue(any(c.get("claim_kind") == "preference" for c in claims))

    def test_extract_policy_claim(self):
        claims = extract_claims("You should always use markdown", "", [])
        self.assertTrue(any(c.get("claim_kind") == "policy" for c in claims))

    def test_extract_commitment_claim(self):
        claims = extract_claims("I will finish the report tomorrow", "", [])
        self.assertTrue(any(c.get("claim_kind") == "commitment" for c in claims))

    def test_extracted_claims_pass_validation(self):
        claims = extract_claims("I prefer Python over Java", "", [])
        for c in claims:
            valid, errors = validate_claim(c)
            self.assertTrue(valid, f"Claim failed validation: {errors}")

    def test_extracted_claims_match_claim_schema(self):
        claims = extract_claims("I love coffee in the mornings", "", [])
        for c in claims:
            obj = Claim.from_dict(c)
            self.assertIn(
                obj.claim_kind,
                {"preference", "identity", "policy", "commitment", "condition", "location", "relationship", "custom"},
            )

    def test_extract_compound_preference_and_timezone(self):
        claims = extract_claims("I prefer Neovim for coding and my timezone is America/Chicago", "", [])
        kinds = [str(c.get("claim_kind") or "") for c in claims]
        slots = [str(c.get("slot") or "") for c in claims]
        self.assertIn("preference", kinds)
        self.assertIn("condition", kinds)
        self.assertTrue(any(s.startswith("preference") for s in slots))
        self.assertIn("timezone", slots)

    def test_extract_location_signal(self):
        claims = extract_claims("I'm currently in Chicago", "", [])
        self.assertTrue(any(c.get("claim_kind") == "location" for c in claims))
        self.assertTrue(any(c.get("slot") == "location" for c in claims))

    def test_user_assistant_boundary_does_not_pollute_timezone_value(self):
        claims = extract_claims(
            "I prefer Neovim for coding and my timezone is America/Chicago",
            "Noted. I will remember this.",
            [],
        )
        tz_claims = [c for c in claims if str(c.get("slot") or "") == "timezone"]
        self.assertEqual(1, len(tz_claims))
        self.assertEqual("America/Chicago", str(tz_claims[0].get("value") or ""))
        self.assertNotIn("Noted", str(tz_claims[0].get("value") or ""))

    def test_dedup_removes_duplicates(self):
        claims = [
            {"subject": "user", "slot": "preference", "id": "1", "claim_kind": "preference", "value": "x", "reason_text": "r", "confidence": 0.5},
            {"subject": "user", "slot": "preference", "id": "2", "claim_kind": "preference", "value": "y", "reason_text": "r", "confidence": 0.5},
        ]
        self.assertEqual(1, len(dedup_claims(claims)))

    def test_validate_missing_field(self):
        claim = {"id": "1", "claim_kind": "preference", "subject": "user"}
        valid, errors = validate_claim(claim)
        self.assertFalse(valid)
        self.assertTrue(any("slot" in e for e in errors))

    def test_validate_empty_reason_text(self):
        claim = {"id": "1", "claim_kind": "preference", "subject": "user", "slot": "food", "value": "pizza", "reason_text": "", "confidence": 0.8}
        valid, _errors = validate_claim(claim)
        self.assertFalse(valid)

    def test_validate_confidence_out_of_range(self):
        claim = {"id": "1", "claim_kind": "preference", "subject": "user", "slot": "food", "value": "pizza", "reason_text": "stated", "confidence": 1.5}
        valid, _errors = validate_claim(claim)
        self.assertFalse(valid)

    def test_infer_claim_kind_no_keywords_returns_custom(self):
        self.assertEqual("custom", infer_claim_kind("the sky is blue"))


if __name__ == "__main__":
    unittest.main()
