import unittest

from core_memory.claim.resolver_helpers import build_claim_timeline, find_conflicts, is_claim_current


class TestClaimResolverHelpers(unittest.TestCase):
    def test_is_current_no_updates(self):
        self.assertTrue(is_claim_current({"id": "c1", "subject": "user", "slot": "pref"}, []))

    def test_is_current_superseded(self):
        self.assertFalse(is_claim_current({"id": "c1"}, [{"decision": "supersede", "target_claim_id": "c1"}]))

    def test_is_current_retracted(self):
        self.assertFalse(is_claim_current({"id": "c1"}, [{"decision": "retract", "target_claim_id": "c1"}]))

    def test_is_current_reaffirm_doesnt_remove(self):
        self.assertTrue(is_claim_current({"id": "c1"}, [{"decision": "reaffirm", "target_claim_id": "c1"}]))

    def test_find_conflicts_empty(self):
        self.assertEqual([], find_conflicts([], []))

    def test_find_conflicts_found(self):
        claims = [{"id": "c1"}, {"id": "c2"}]
        updates = [{"decision": "conflict", "target_claim_id": "c1"}]
        result = find_conflicts(claims, updates)
        self.assertEqual(1, len(result))
        self.assertEqual("c1", result[0]["id"])

    def test_build_timeline_structure(self):
        claims = [{"id": "c1", "subject": "user", "slot": "pref", "value": "coffee"}]
        updates = [{"decision": "retract", "target_claim_id": "c1"}]
        timeline = build_claim_timeline(claims, updates)
        self.assertEqual(2, len(timeline))
        event_types = {e["event_type"] for e in timeline}
        self.assertIn("assert", event_types)
        self.assertIn("retract", event_types)


if __name__ == "__main__":
    unittest.main()
