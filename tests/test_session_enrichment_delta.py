import unittest

from core_memory.runtime.session_enrichment_delta import (
    SCHEMA,
    build_window_context_ref,
    crawler_updates_to_delta,
    delta_to_crawler_updates,
)


class TestSessionEnrichmentDeltaWindow(unittest.TestCase):
    def test_window_context_ref_captures_explicit_bounds(self):
        ctx = {
            "session_id": "s1",
            "beads": [
                {"id": "b1", "source_turn_ids": ["t1"]},
                {"id": "b2", "source_turn_ids": ["t2"]},
            ],
            "visible_bead_ids": ["b1", "b2", "carry-1"],
            "carry_in_bead_ids": ["carry-1"],
        }

        ref = build_window_context_ref(session_id="s1", crawler_ctx=ctx, window_turn_ids=["t1", "t2"])

        self.assertEqual("s1", ref["session_id"])
        self.assertEqual(2, ref["row_count"])
        self.assertEqual("b1", ref["first_visible_bead_id"])
        self.assertEqual("carry-1", ref["last_visible_bead_id"])
        self.assertEqual("t1", ref["first_visible_turn_id"])
        self.assertEqual("t2", ref["last_visible_turn_id"])
        self.assertIn("carry-1", ref["carry_in_bead_ids"])
        self.assertTrue(str(ref["context_fingerprint"]).startswith("sha256:"))


class TestSessionEnrichmentDeltaAdapter(unittest.TestCase):
    def test_crawler_updates_roundtrip_preserves_current_shape(self):
        updates = {
            "beads_create": [
                {
                    "type": "decision",
                    "title": "Use JSONB",
                    "summary": ["PostgreSQL JSONB keeps writes transactional"],
                    "source_turn_ids": ["t1"],
                    "tags": ["crawler_reviewed"],
                }
            ],
            "promotions": ["b1"],
            "associations": [
                {
                    "source_bead_id": "b2",
                    "target_bead_id": "b1",
                    "relationship": "supports",
                    "reason_text": "The second turn supports the first decision.",
                    "confidence": 0.91,
                    "provenance": "model_inferred",
                    "evidence_fields": ["summary"],
                }
            ],
            "association_lifecycle": [
                {
                    "association_id": "assoc-1",
                    "action": "reaffirm",
                    "reason_text": "Still valid.",
                    "confidence": 0.8,
                }
            ],
        }

        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates=updates,
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
            idempotency_key="enrich-s1-t1",
        )
        projected = delta_to_crawler_updates(delta)

        self.assertEqual(SCHEMA, delta["schema"])
        self.assertEqual("enrich-s1-t1", delta["source"]["idempotency_key"])
        self.assertTrue(delta["beads_create"][0]["dedupe_key"].startswith("bead:s1:t1:decision:"))
        self.assertEqual("assoc:b2:b1:supports", delta["associations"][0]["dedupe_key"])
        self.assertEqual(["b1"], projected["promotions"])
        self.assertEqual("supports", projected["associations"][0]["relationship"])
        self.assertEqual("assoc-1", projected["association_lifecycle"][0]["association_id"])

    def test_invalid_association_is_quarantined_not_projected(self):
        delta = crawler_updates_to_delta(
            session_id="s1",
            turn_id="t1",
            updates={"associations": [{"source_bead_id": "b2", "relationship": "supports"}]},
            crawler_ctx={"session_id": "s1", "visible_bead_ids": ["b1", "b2"]},
        )
        projected = delta_to_crawler_updates(delta)
        quarantine = delta["diagnostics"]["quarantine"]

        self.assertEqual([], projected["associations"])
        self.assertEqual(1, delta["diagnostics"]["quarantined"])
        self.assertEqual("associations", quarantine[0]["row_type"])
        self.assertIn("missing_source_target_or_relationship", quarantine[0]["reasons"])

    def test_array_bounds_quarantine_overflow(self):
        updates = {"promotions": [f"b{i}" for i in range(70)]}
        delta = crawler_updates_to_delta(session_id="s1", turn_id="t1", updates=updates)

        self.assertEqual(64, len(delta["promotions"]))
        self.assertEqual(6, delta["diagnostics"]["quarantined"])
        self.assertEqual("array_bound_exceeded", delta["diagnostics"]["quarantine"][0]["reasons"][0])


if __name__ == "__main__":
    unittest.main()
