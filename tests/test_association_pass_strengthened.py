import unittest

from core_memory.association import run_association_pass
from core_memory.schema.models import RelationshipType


class TestAssociationPassStrengthened(unittest.TestCase):
    def test_session_relative_weighting_prefers_same_session(self):
        idx = {
            "beads": {
                "old_same": {
                    "id": "old_same",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "promotion decision",
                    "summary": ["candidate only"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s1",
                },
                "old_other": {
                    "id": "old_other",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "title": "promotion decision",
                    "summary": ["candidate only"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s2",
                },
            }
        }
        bead = {
            "id": "new",
            "created_at": "2026-01-03T00:00:00+00:00",
            "title": "promotion decision",
            "summary": ["candidate only"],
            "tags": ["promotion_workflow"],
            "session_id": "s1",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=2)
        self.assertEqual("old_same", out[0]["other_id"])

    def test_causal_typing_can_emit_supports(self):
        idx = {
            "beads": {
                "b1": {
                    "id": "b1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "because promotion inflation caused compaction issues",
                    "summary": ["led to gating"],
                    "tags": ["promotion_workflow"],
                    "session_id": "s1",
                }
            }
        }
        bead = {
            "id": "b2",
            "created_at": "2026-01-02T00:00:00+00:00",
            "title": "because we blocked broad promotion",
            "summary": ["therefore candidate-only"],
            "tags": ["promotion_workflow"],
            "session_id": "s1",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=1)
        self.assertEqual("supports", out[0]["relationship"])
        self.assertEqual("causal_overlap_same_session", out[0]["reason_code"])

    def test_generic_fallback_uses_canonical_associated_with_with_reason_code(self):
        idx = {
            "beads": {
                "b1": {
                    "id": "b1",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "vector manifest",
                    "summary": ["queue checkpoint"],
                    "tags": ["semantic_index"],
                    "session_id": "s1",
                }
            }
        }
        bead = {
            "id": "b2",
            "created_at": "2026-01-02T00:00:00+00:00",
            "title": "recall manifest",
            "summary": ["queue checkpoint"],
            "tags": ["semantic_index"],
            "session_id": "s2",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=1)
        self.assertEqual("associated_with", out[0]["relationship"])
        self.assertEqual("shared_tag_overlap", out[0]["reason_code"])
        self.assertNotIn(out[0]["relationship"], {"related", "shared_tag"})

    def test_cross_session_causal_typing_uses_directional_schema_types(self):
        idx = {
            "beads": {
                "older": {
                    "id": "older",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "title": "timeout caused queue retries",
                    "summary": ["semantic worker"],
                    "tags": ["queue"],
                    "session_id": "s1",
                },
                "newer": {
                    "id": "newer",
                    "created_at": "2026-01-03T00:00:00+00:00",
                    "title": "semantic worker led to stable drains",
                    "summary": ["queue"],
                    "tags": ["queue"],
                    "session_id": "s3",
                },
            }
        }

        newer_source = {
            "id": "current-newer",
            "created_at": "2026-01-02T00:00:00+00:00",
            "title": "because queue retries blocked drains",
            "summary": ["semantic worker"],
            "tags": ["queue"],
            "session_id": "s2",
        }
        older_source = dict(newer_source, id="current-older", created_at="2025-12-31T00:00:00+00:00")

        caused_by = run_association_pass({"beads": {"older": idx["beads"]["older"]}}, newer_source, max_lookback=10, top_k=1)
        led_to = run_association_pass({"beads": {"newer": idx["beads"]["newer"]}}, older_source, max_lookback=10, top_k=1)

        self.assertEqual("caused_by", caused_by[0]["relationship"])
        self.assertEqual("causal_cross_session_source_follows_target", caused_by[0]["reason_code"])
        self.assertEqual("led_to", led_to[0]["relationship"])
        self.assertEqual("causal_cross_session_source_precedes_target", led_to[0]["reason_code"])

    def test_temporal_typing_can_emit_precedes_for_older_source(self):
        idx = {
            "beads": {
                "later": {
                    "id": "later",
                    "created_at": "2026-01-02T00:00:00+00:00",
                    "title": "handoff checklist",
                    "summary": ["worker queue"],
                    "tags": [],
                    "session_id": "s1",
                }
            }
        }
        bead = {
            "id": "earlier",
            "created_at": "2026-01-01T00:00:00+00:00",
            "title": "handoff checklist",
            "summary": ["worker queue"],
            "tags": [],
            "session_id": "s1",
        }

        out = run_association_pass(idx, bead, max_lookback=10, top_k=1)
        self.assertEqual("precedes", out[0]["relationship"])
        self.assertEqual("temporal_precedes", out[0]["reason_code"])

    def test_preview_relationships_stay_within_schema_enum(self):
        schema_values = {x.value for x in RelationshipType}
        self.assertIn("associated_with", schema_values)
        self.assertIn("caused_by", schema_values)
        self.assertIn("led_to", schema_values)
        self.assertIn("precedes", schema_values)
        self.assertNotIn("related", schema_values)
        self.assertNotIn("shared_tag", schema_values)


if __name__ == "__main__":
    unittest.main()
