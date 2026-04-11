import unittest

from core_memory.temporal.resolution import claim_temporal_sort_key, claim_visible_as_of, normalize_as_of, parse_timestamp, update_visible_as_of


class TestTemporalResolutionHelpers(unittest.TestCase):
    def test_parse_timestamp_z_and_naive(self):
        self.assertIsNotNone(parse_timestamp("2026-01-01T00:00:00Z"))
        self.assertIsNotNone(parse_timestamp("2026-01-01T00:00:00"))

    def test_claim_visible_as_of_interval(self):
        claim = {
            "effective_from": "2026-01-01T00:00:00Z",
            "effective_to": "2026-01-10T00:00:00Z",
        }
        self.assertTrue(claim_visible_as_of(claim, normalize_as_of("2026-01-05T00:00:00Z")))
        self.assertFalse(claim_visible_as_of(claim, normalize_as_of("2025-12-31T23:00:00Z")))
        # effective_to is exclusive
        self.assertFalse(claim_visible_as_of(claim, normalize_as_of("2026-01-10T00:00:00Z")))

    def test_update_visible_as_of_defaults_true_without_timestamp(self):
        self.assertTrue(update_visible_as_of({"decision": "retract"}, normalize_as_of("2026-01-01T00:00:00Z")))

    def test_temporal_sort_key_stable(self):
        a = {"id": "a", "effective_from": "2026-01-01T00:00:00Z"}
        b = {"id": "b", "effective_from": "2026-01-02T00:00:00Z"}
        self.assertLess(claim_temporal_sort_key(a), claim_temporal_sort_key(b))


if __name__ == "__main__":
    unittest.main()
