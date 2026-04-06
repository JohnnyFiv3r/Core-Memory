from __future__ import annotations

import unittest
from dataclasses import fields

from core_memory.schema.models import Association, Bead, Event


class TestSchemaModelSerializationSlice51B(unittest.TestCase):
    def test_to_dict_keys_match_dataclass_fields(self):
        bead = Bead(id="b1", type="decision", title="Ship it")
        assoc = Association(id="a1", source_bead="b1", target_bead="b2", relationship="supports")
        event = Event(id="e1", event_type="turn_finalized", session_id="s1", payload={"ok": True})

        self.assertEqual({f.name for f in fields(Bead)}, set(bead.to_dict().keys()))
        self.assertEqual({f.name for f in fields(Association)}, set(assoc.to_dict().keys()))
        self.assertEqual({f.name for f in fields(Event)}, set(event.to_dict().keys()))

    def test_from_dict_ignores_unknown_and_does_not_share_mutable_input(self):
        payload = {
            "id": "b2",
            "type": "lesson",
            "title": "Input aliasing",
            "summary": ["first"],
            "tags": ["schema"],
            "links": {"k": "v"},
            "retrieval_eligible": True,
            "retrieval_title": "aliasing",
            "retrieval_facts": ["fact"],
            "because": ["reason"],
            "unknown_field": "should_be_dropped",
        }
        bead = Bead.from_dict(payload)

        # Unknown keys are discarded.
        self.assertNotIn("unknown_field", bead.to_dict())

        # Mutating the source payload should not mutate the bead instance.
        payload["summary"].append("mutated")
        payload["tags"].append("mutated")
        payload["links"]["k2"] = "v2"

        self.assertEqual(["first"], bead.summary)
        self.assertEqual(["schema"], bead.tags)
        self.assertEqual({"k": "v"}, bead.links)

    def test_to_dict_returns_detached_mutable_values(self):
        bead = Bead(id="b3", type="context", title="detached")
        out = bead.to_dict()
        out["summary"].append("changed")
        out["tags"].append("changed")

        self.assertEqual([], bead.summary)
        self.assertEqual([], bead.tags)


if __name__ == "__main__":
    unittest.main()
