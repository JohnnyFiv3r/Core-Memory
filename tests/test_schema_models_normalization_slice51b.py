from __future__ import annotations

import unittest

from core_memory.schema.models import Association, Bead, Event


class TestSchemaModelsNormalizationSlice51B(unittest.TestCase):
    def test_bead_from_dict_normalizes_vocab_and_shapes(self):
        bead = Bead.from_dict(
            {
                "id": "b1",
                "type": "PROMOTED_LESSON",
                "title": "Normalization",
                "scope": "GLOBAL",
                "authority": "USER_CONFIRMED",
                "status": "not_real",
                "impact_level": "MEGA",
                "summary": "single",
                "tags": "schema",
                "links": "not_dict",
                "confidence": "1.7",
                "uncertainty": "-0.2",
                "recall_count": "-5",
                "retrieval_eligible": True,
                "retrieval_title": "",
                "retrieval_facts": [],
            }
        )

        self.assertEqual("lesson", bead.type)
        self.assertEqual("global", bead.scope)
        self.assertEqual("user_confirmed", bead.authority)
        self.assertEqual("open", bead.status)
        self.assertIsNone(bead.impact_level)

        self.assertEqual(["single"], bead.summary)
        self.assertEqual(["schema"], bead.tags)
        self.assertEqual({}, bead.links)

        self.assertEqual(1.0, bead.confidence)
        self.assertEqual(0.0, bead.uncertainty)
        self.assertEqual(0, bead.recall_count)

        # Existing invariant: eligibility is dropped when quality is insufficient.
        self.assertFalse(bead.retrieval_eligible)

    def test_association_from_dict_normalizes_relationship_and_numeric_bounds(self):
        assoc = Association.from_dict(
            {
                "id": "a1",
                "source_bead": "b1",
                "target_bead": "b2",
                "relationship": "shared_tag",
                "novelty": "-1",
                "confidence": "2",
                "decay_score": "-3.5",
                "reinforced_count": "-7",
            }
        )

        self.assertEqual("associated_with", assoc.relationship)
        self.assertEqual(0.0, assoc.novelty)
        self.assertEqual(1.0, assoc.confidence)
        self.assertEqual(0.0, assoc.decay_score)
        self.assertEqual(0, assoc.reinforced_count)

    def test_event_from_dict_normalizes_payload_shape(self):
        ev = Event.from_dict(
            {
                "id": "e1",
                "event_type": "turn",
                "session_id": "s1",
                "payload": "not_dict",
            }
        )
        self.assertEqual({}, ev.payload)


if __name__ == "__main__":
    unittest.main()
