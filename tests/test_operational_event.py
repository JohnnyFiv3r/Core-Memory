import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CORE_MEMORY_SEMANTIC_AUTODRAIN", "off")

from core_memory.runtime.ingest.external_evidence import (
    ingest_operational_event,
    resolve_external_bead_type,
)
from core_memory.schema.models import BeadType
from core_memory.schema.normalization import is_allowed_bead_type


def _event_payload(**overrides):
    payload = {
        "data_type_flag": "operational_event",
        "title": "Deal 991 moved to Proposal",
        "summary": ["Deal 991 (Acme Corp) advanced from Discovery to Proposal."],
        "source_id": "hubspot:portal-1",
        "source_event_id": "hs-evt-001",
        "source_system": "hubspot",
        "source_table": "deals",
        "source_record_id": "991",
        "business_object_type": "deal",
        "business_object_id": "991",
        "record_action": "stage_changed",
        "actor": "rep@acme.com",
        "state_change": {"from": "discovery", "to": "proposal"},
        "entities": ["Acme Corp"],
        "entity_refs": ["Acme Corp"],
        "attribute_tags": ["sales", "deal_stage"],
        "as_of_timestamp": "2026-06-12T15:00:00Z",
        "core_memory_unifying_id": "hubspot:deal:991",
        "hydration_ref": {"store": "hubspot", "ref": "deals/991"},
    }
    payload.update(overrides)
    return payload


class TestOperationalEventType(unittest.TestCase):
    def test_canonical_type(self):
        self.assertEqual(BeadType.OPERATIONAL_EVENT.value, "operational_event")
        self.assertTrue(is_allowed_bead_type("operational_event"))

    def test_flag_routes_to_operational_event(self):
        self.assertEqual("operational_event", resolve_external_bead_type({"data_type_flag": "operational_event"}))
        self.assertEqual("operational_event", resolve_external_bead_type({"data_type_flag": "state_transition"}))
        self.assertEqual("operational_event", resolve_external_bead_type({"source_kind": "operational"}))

    def test_writes_typed_transition_fields(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = ingest_operational_event(td, _event_payload(), session_id="ops")
            self.assertEqual("accepted", receipt["status"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = idx["beads"][receipt["bead_id"]]
            self.assertEqual("operational_event", bead["type"])
            self.assertEqual("deal", bead["business_object_type"])
            self.assertEqual("991", bead["business_object_id"])
            self.assertEqual("stage_changed", bead["record_action"])
            self.assertEqual("rep@acme.com", bead["actor"])
            self.assertEqual({"from": "discovery", "to": "proposal"}, bead["state_change"])
            self.assertEqual("source_attributed", bead["authority"])


class TestOperationalEventsAccumulate(unittest.TestCase):
    """The defining invariant: transitions of the same business object are
    history — they coexist as the worldline substrate, never superseding each
    other (unlike documents/records, where a new version closes the prior)."""

    def test_sibling_transitions_coexist_no_supersession(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_operational_event(td, _event_payload(), session_id="ops")
            second = ingest_operational_event(
                td,
                _event_payload(
                    source_event_id="hs-evt-002",
                    title="Deal 991 moved to Negotiation",
                    summary=["Deal 991 advanced from Proposal to Negotiation."],
                    record_action="stage_changed",
                    state_change={"from": "proposal", "to": "negotiation"},
                    as_of_timestamp="2026-06-12T16:00:00Z",
                ),
                session_id="ops",
            )
            self.assertEqual("accepted", second["status"])
            self.assertNotEqual(first["bead_id"], second["bead_id"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(idx["beads"]))
            # Neither event was superseded; both remain current truth.
            for bid in (first["bead_id"], second["bead_id"]):
                self.assertNotEqual("superseded", idx["beads"][bid]["status"])
                self.assertEqual([], idx["beads"][bid].get("superseded_by", []))

    def test_same_event_id_redelivery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            first = ingest_operational_event(td, _event_payload(), session_id="ops")
            replay = ingest_operational_event(td, _event_payload(), session_id="ops")
            self.assertEqual("already_exists", replay["status"])
            self.assertEqual(first["bead_id"], replay["bead_id"])
            idx = json.loads((Path(td) / ".beads" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(idx["beads"]))


class TestOperationalEventValidation(unittest.TestCase):
    def test_requires_record_action(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError) as ctx:
                ingest_operational_event(td, _event_payload(record_action=""), session_id="ops")
            self.assertIn("record_action", str(ctx.exception))

    def test_requires_business_object_or_record_id(self):
        with tempfile.TemporaryDirectory() as td:
            payload = _event_payload(business_object_id="", source_record_id="")
            with self.assertRaises(ValueError) as ctx:
                ingest_operational_event(td, payload, session_id="ops")
            self.assertIn("business_object_id", str(ctx.exception))

    def test_requires_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            payload = _event_payload(as_of_timestamp="", occurred_at="", observed_at="")
            with self.assertRaises(ValueError) as ctx:
                ingest_operational_event(td, payload, session_id="ops")
            self.assertIn("as_of_timestamp", str(ctx.exception))

    def test_requires_entities(self):
        with tempfile.TemporaryDirectory() as td:
            payload = _event_payload(entities=[], entity_refs=[])
            with self.assertRaises(ValueError) as ctx:
                ingest_operational_event(td, payload, session_id="ops")
            self.assertIn("entities", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
