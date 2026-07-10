from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from core_memory.association.crawler_contract import (
    _normalize_creation_rows,
    apply_crawler_updates,
)
from core_memory.runtime.passes.agent_authored_contract import validate_agent_authored_updates
from core_memory.schema.agent_authored_updates import (
    AGENT_OWNED_BEAD_FIELDS,
    COMPATIBILITY_BEAD_FIELDS,
    RUNTIME_OWNED_BEAD_FIELDS,
    bead_field_ownership_snapshot,
)
from core_memory.schema.models import Bead


def _stored_bead(root: str, bead_id: str) -> dict:
    index = json.loads(
        (Path(root) / ".beads" / "index.json").read_text(encoding="utf-8")
    )
    return dict((index.get("beads") or {})[bead_id])


class TestAgentLedWriteIntegrity(unittest.TestCase):
    def test_bead_field_ownership_is_complete_and_disjoint(self):
        all_fields = {field.name for field in fields(Bead)}
        inventory = bead_field_ownership_snapshot()

        self.assertEqual(
            all_fields,
            set(inventory["agent_owned"])
            | set(inventory["runtime_owned"])
            | set(inventory["compatibility"]),
        )
        self.assertFalse(AGENT_OWNED_BEAD_FIELDS & RUNTIME_OWNED_BEAD_FIELDS)
        self.assertFalse(AGENT_OWNED_BEAD_FIELDS & COMPATIBILITY_BEAD_FIELDS)
        self.assertFalse(RUNTIME_OWNED_BEAD_FIELDS & COMPATIBILITY_BEAD_FIELDS)

    def test_schema_driven_normalizer_keeps_every_agent_owned_field(self):
        authored = Bead(id="ignored", type="context", title="Complete contract").to_dict()
        authored = {
            key: value for key, value in authored.items() if key in AGENT_OWNED_BEAD_FIELDS
        }
        authored.update(
            {
                "creation_role": "current_turn",
                "source_turn_ids": ["t1"],
                "summary": ["All canonical semantic fields traverse the normalizer."],
                "entities": ["Core Memory"],
                "retrieval_eligible": False,
            }
        )

        rows = _normalize_creation_rows({"beads_create": [authored]})

        self.assertEqual(1, len(rows))
        self.assertTrue(AGENT_OWNED_BEAD_FIELDS.issubset(rows[0]))
        self.assertFalse(rows[0]["retrieval_eligible"])

    def test_rich_agent_fields_round_trip_through_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            authored = Bead(id="ignored", type="decision", title="Use append-only revisions").to_dict()
            authored = {
                key: value for key, value in authored.items() if key in AGENT_OWNED_BEAD_FIELDS
            }
            authored.update(
                {
                    "creation_role": "current_turn",
                    "source_turn_ids": ["t-rich"],
                    "summary": ["Richer interpretations are appended."],
                    "because": ["Existing evidence anchors remain immutable."],
                    "retrieval_eligible": False,
                    "retrieval_title": "Append-only memory revision policy",
                    "retrieval_facts": ["Existing evidence anchors remain immutable."],
                    "entities": ["Core Memory"],
                    "decision_keys": ["memory.revision.append_only"],
                    "claims": [{"text": "Revisions are append-only."}],
                    "state_change": {"from": "mutable", "to": "append_only"},
                    "authority": "agent_inferred",
                    "confidence": 0.91,
                }
            )

            receipt = apply_crawler_updates(root, "s-rich", {"beads_create": [authored]})
            stored = _stored_bead(root, receipt["current_turn_bead_id"])

            self.assertTrue(AGENT_OWNED_BEAD_FIELDS.issubset(stored))
            self.assertFalse(stored["retrieval_eligible"])
            self.assertEqual("Append-only memory revision policy", stored["retrieval_title"])
            self.assertEqual(["memory.revision.append_only"], stored["decision_keys"])
            self.assertEqual([{"text": "Revisions are append-only."}], stored["claims"])
            self.assertEqual({"from": "mutable", "to": "append_only"}, stored["state_change"])

    def test_legacy_state_change_string_is_preserved_as_description(self):
        with tempfile.TemporaryDirectory() as root:
            receipt = apply_crawler_updates(
                root,
                "s1",
                {
                    "beads_create": [
                        {
                            "creation_role": "current_turn",
                            "type": "context",
                            "title": "Approval recorded",
                            "summary": ["The proposal is approved."],
                            "source_turn_ids": ["t1"],
                            "state_change": "proposal moved to approved",
                        }
                    ]
                },
            )
            stored = _stored_bead(root, receipt["current_turn_bead_id"])
            self.assertEqual(
                {"description": "proposal moved to approved"},
                stored["state_change"],
            )

    def test_primary_and_derived_rows_commit_in_primary_first_order(self):
        with tempfile.TemporaryDirectory() as root:
            receipt = apply_crawler_updates(
                root,
                "s1",
                {
                    "beads_create": [
                        {
                            "creation_role": "derived",
                            "derived_from_bead_ids": ["$current_turn"],
                            "type": "lesson",
                            "title": "Preserve authored richness",
                            "summary": ["Typed guardrails must not become semantic authors."],
                            "because": ["The narrow whitelist discarded grounded fields."],
                            "entities": ["Core Memory"],
                        },
                        {
                            "creation_role": "current_turn",
                            "type": "decision",
                            "title": "Replace the creation whitelist",
                            "summary": ["Use the canonical schema field inventory."],
                            "because": ["Known authored fields must survive persistence."],
                            "source_turn_ids": ["t1"],
                            "entities": ["Core Memory"],
                        },
                    ]
                },
            )

            self.assertEqual(2, receipt["beads_created"])
            self.assertEqual([], receipt["derived_failures"])
            self.assertEqual(1, len(receipt["derived_bead_ids"]))
            self.assertEqual(
                receipt["current_turn_bead_id"],
                receipt["created_bead_ids"][0],
            )
            derived = _stored_bead(root, receipt["derived_bead_ids"][0])
            self.assertEqual(
                [receipt["current_turn_bead_id"]],
                derived["derived_from_bead_ids"],
            )
            self.assertNotIn("creation_role", derived)

    def test_derived_failure_does_not_erase_committed_primary(self):
        with tempfile.TemporaryDirectory() as root:
            receipt = apply_crawler_updates(
                root,
                "s1",
                {
                    "beads_create": [
                        {
                            "creation_role": "current_turn",
                            "type": "decision",
                            "title": "Commit the canonical decision",
                            "summary": ["The primary remains canonical."],
                            "because": ["Derived writes are independently reportable."],
                            "source_turn_ids": ["t1"],
                            "retrieval_eligible": False,
                        },
                        {
                            "creation_role": "derived",
                            "derived_from_bead_ids": ["$current_turn"],
                            "type": "lesson",
                            "title": "Invalid derived companion",
                            "summary": ["This row fails persistence validation."],
                            "because": ["context_tags has the wrong shape."],
                            "retrieval_eligible": False,
                            "context_tags": "not-a-list",
                        },
                    ]
                },
            )

            self.assertEqual(1, receipt["beads_created"])
            self.assertTrue(receipt["current_turn_bead_id"])
            self.assertEqual([], receipt["derived_bead_ids"])
            self.assertEqual(
                "derived_bead_persistence_failed",
                receipt["derived_failures"][0]["code"],
            )
            stored = _stored_bead(root, receipt["current_turn_bead_id"])
            self.assertEqual("Commit the canonical decision", stored["title"])

    def test_warn_drops_unknown_fields_and_hard_validation_rejects_them(self):
        payload = {
            "beads_create": [
                {
                    "creation_role": "current_turn",
                    "type": "context",
                    "title": "Known fields only",
                    "summary": ["Unknown fields are visible, never stored."],
                    "source_turn_ids": ["t1"],
                    "entities": ["Core Memory"],
                    "invented_semantic_field": "must not persist",
                }
            ]
        }

        ok, code, details = validate_agent_authored_updates(payload)
        self.assertFalse(ok)
        self.assertEqual("agent_updates_invalid", code)
        self.assertEqual(["invented_semantic_field"], details["unknown_fields"])

        with tempfile.TemporaryDirectory() as root:
            receipt = apply_crawler_updates(root, "s1", payload)
            stored = _stored_bead(root, receipt["current_turn_bead_id"])
            self.assertEqual(
                ["invented_semantic_field"],
                receipt["creation_dropped_fields"],
            )
            self.assertNotIn("invented_semantic_field", stored)
            self.assertIn(
                "unknown_authored_field:dropped:invented_semantic_field",
                stored["validation_warnings"],
            )

    def test_generic_title_only_downgrades_and_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            receipt = apply_crawler_updates(
                root,
                "s1",
                {
                    "beads_create": [
                        {
                            "creation_role": "current_turn",
                            "type": "context",
                            "title": "Reply",
                            "summary": ["Compatibility title remains authored."],
                            "source_turn_ids": ["t1"],
                            "retrieval_eligible": True,
                        }
                    ]
                },
            )
            stored = _stored_bead(root, receipt["current_turn_bead_id"])
            self.assertEqual("Reply", stored["title"])
            self.assertFalse(stored["retrieval_eligible"])
            self.assertIn(
                "retrieval_eligible:downgraded_generic_title",
                stored["validation_warnings"],
            )
            self.assertIn(
                {
                    "row_index": 0,
                    "code": "retrieval_eligibility_downgraded",
                    "reason": "generic_title",
                },
                receipt["creation_diagnostics"],
            )

    def test_bead_model_preserves_false_and_state_change_string(self):
        bead = Bead.from_dict(
            {
                "id": "b1",
                "type": "decision",
                "title": "Preserve false",
                "retrieval_eligible": False,
                "state_change": "draft became approved",
            }
        )
        self.assertFalse(bead.retrieval_eligible)
        self.assertEqual(
            {"description": "draft became approved"},
            bead.state_change,
        )


if __name__ == "__main__":
    unittest.main()
