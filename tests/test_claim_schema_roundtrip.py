import unittest

from core_memory.schema.models import Claim, ClaimUpdate


class TestClaimSchemaRoundtrip(unittest.TestCase):
    def test_claim_roundtrip(self):
        src = {
            "id": "c1",
            "claim_kind": "preference",
            "subject": "user",
            "slot": "language",
            "value": "python",
            "reason_text": "stated in turn",
            "confidence": 0.9,
            "effective_from": "2026-01-01T00:00:00Z",
            "effective_to": "2026-01-10T00:00:00Z",
        }
        obj = Claim.from_dict(src)
        out = obj.to_dict()
        self.assertEqual("c1", out.get("id"))
        self.assertEqual("preference", out.get("claim_kind"))
        self.assertEqual("user", out.get("subject"))
        self.assertEqual("language", out.get("slot"))
        self.assertEqual("2026-01-01T00:00:00Z", out.get("effective_from"))
        self.assertEqual("2026-01-10T00:00:00Z", out.get("effective_to"))

    def test_claim_update_roundtrip_operational_shape(self):
        src = {
            "id": "u1",
            "decision": "supersede",
            "target_claim_id": "c1",
            "replacement_claim_id": "c2",
            "subject": "user",
            "slot": "timezone",
            "reason_text": "new information",
            "confidence": 0.88,
            "trigger_bead_id": "b2",
        }
        obj = ClaimUpdate.from_dict(src)
        out = obj.to_dict()
        self.assertEqual("u1", out.get("id"))
        self.assertEqual("supersede", out.get("decision"))
        self.assertEqual("c1", out.get("target_claim_id"))
        self.assertEqual("c2", out.get("replacement_claim_id"))
        self.assertEqual("user", out.get("subject"))
        self.assertEqual("timezone", out.get("slot"))

    def test_claim_update_accepts_legacy_alias_keys(self):
        src = {
            "id": "u2",
            "decision": "supersede",
            "claim_id": "c_old",
            "successor_claim_id": "c_new",
            "subject": "user",
            "slot": "preference",
            "reason_text": "legacy payload",
            "confidence": 0.7,
        }
        obj = ClaimUpdate.from_dict(src)
        out = obj.to_dict()
        self.assertEqual("c_old", out.get("target_claim_id"))
        self.assertEqual("c_new", out.get("replacement_claim_id"))


if __name__ == "__main__":
    unittest.main()
