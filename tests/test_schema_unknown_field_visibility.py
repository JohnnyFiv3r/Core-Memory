from __future__ import annotations

import unittest

from core_memory.schema.models import (
    Association,
    Bead,
    Event,
    reset_schema_unknown_field_counters,
    schema_unknown_field_counters,
)


class TestSchemaUnknownFieldVisibilitySlice56A(unittest.TestCase):
    def setUp(self) -> None:
        reset_schema_unknown_field_counters()

    def test_bead_from_dict_logs_and_counts_unknown_fields(self):
        payload = {
            "id": "b1",
            "type": "decision",
            "title": "ship",
            "unknown_alpha": 1,
            "unknown_beta": 2,
        }
        with self.assertLogs("core_memory.schema.models", level="DEBUG") as logs:
            bead = Bead.from_dict(payload)

        self.assertEqual("b1", bead.id)
        text = "\n".join(logs.output)
        self.assertIn("Dropping unknown Bead fields", text)
        self.assertIn("unknown_alpha", text)
        self.assertIn("unknown_beta", text)

        counters = schema_unknown_field_counters()
        self.assertEqual(1, (counters.get("Bead") or {}).get("unknown_alpha"))
        self.assertEqual(1, (counters.get("Bead") or {}).get("unknown_beta"))

    def test_association_and_event_unknowns_are_tracked_per_model(self):
        with self.assertLogs("core_memory.schema.models", level="DEBUG"):
            Association.from_dict(
                {
                    "id": "a1",
                    "source_bead": "b1",
                    "target_bead": "b2",
                    "relationship": "supports",
                    "unexpected": "x",
                }
            )
            Event.from_dict(
                {
                    "id": "e1",
                    "event_type": "turn",
                    "session_id": "s1",
                    "payload": {},
                    "extra": True,
                }
            )

        counters = schema_unknown_field_counters()
        self.assertEqual(1, (counters.get("Association") or {}).get("unexpected"))
        self.assertEqual(1, (counters.get("Event") or {}).get("extra"))

    def test_no_unknown_fields_produces_no_counters(self):
        Bead.from_dict({"id": "b2", "type": "context", "title": "ok"})
        self.assertEqual({}, schema_unknown_field_counters())


if __name__ == "__main__":
    unittest.main()
