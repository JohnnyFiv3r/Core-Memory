"""Tests for preview-classifier fallback in apply_crawler_updates (TODO #3)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone


def _make_store(td: str):
    from core_memory.persistence.store import MemoryStore
    return MemoryStore(td)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestClassifierFallback(unittest.TestCase):
    def _setup_store_with_two_beads(self, td):
        s = _make_store(td)
        b1 = s.add_bead(
            type="context",
            title="Database migration caused deployment failure",
            summary=["Ran migration on prod, caused downtime"],
            session_id="sess-1",
            source_turn_ids=["t1"],
            tags=["database", "migration"],
        )
        b2 = s.add_bead(
            type="context",
            title="Deployment rollback resolved incident",
            summary=["Rolled back deployment, services recovered"],
            session_id="sess-1",
            source_turn_ids=["t2"],
            tags=["database", "rollback"],
        )
        return s, b1, b2

    def test_empty_relationship_gets_filled_by_classifier(self):
        """An association with empty relationship should receive a classifier-inferred type."""
        with tempfile.TemporaryDirectory() as td:
            s, b1, b2 = self._setup_store_with_two_beads(td)

            from core_memory.association.crawler_contract import apply_crawler_updates
            result = apply_crawler_updates(
                td,
                "sess-1",
                {
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "",
                            "reason_text": "bead pairs are related",
                            "confidence": 0.7,
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )
            # Classifier fills the relationship, so the association should be queued (not quarantined)
            self.assertEqual(result.get("associations_appended"), 1, result)
            self.assertEqual(result.get("associations_quarantined"), 0, result)

    def test_none_relationship_gets_filled_by_classifier(self):
        """An association with None relationship should receive a classifier-inferred type."""
        with tempfile.TemporaryDirectory() as td:
            s, b1, b2 = self._setup_store_with_two_beads(td)

            from core_memory.association.crawler_contract import apply_crawler_updates
            result = apply_crawler_updates(
                td,
                "sess-1",
                {
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": None,
                            "reason_text": "bead pairs are related",
                            "confidence": 0.7,
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )
            self.assertEqual(result.get("associations_appended"), 1, result)

    def test_explicit_relationship_is_not_overridden(self):
        """Agent-supplied relationships must be preserved; classifier must not fire."""
        with tempfile.TemporaryDirectory() as td:
            s, b1, b2 = self._setup_store_with_two_beads(td)

            from core_memory.association.crawler_contract import apply_crawler_updates
            result = apply_crawler_updates(
                td,
                "sess-1",
                {
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "causes",
                            "reason_text": "explicit agent decision",
                            "confidence": 0.9,
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )
            self.assertEqual(result.get("associations_appended"), 1, result)
            # Verify the explicit legacy relationship is accepted and stored canonically.
            idx_path = Path(td) / ".beads" / "index.json"
            from core_memory.association.crawler_contract import merge_crawler_updates
            merge_crawler_updates(td, "sess-1")
            idx = json.loads(idx_path.read_text())
            assocs = [a for a in idx.get("associations", [])
                      if a.get("source_bead") == b1 and a.get("target_bead") == b2]
            self.assertTrue(any(a.get("relationship") == "causes" for a in assocs), assocs)

    def test_infer_relationship_public_api(self):
        """infer_relationship returns a canonical relationship and reason_code."""
        from core_memory.association.preview import infer_relationship
        bead_a = {
            "id": "b1",
            "title": "Deployment failed because migration ran",
            "summary": ["caused by database lock"],
            "tags": ["deploy"],
            "session_id": "s1",
            "created_at": "2026-01-01T10:00:00Z",
        }
        bead_b = {
            "id": "b2",
            "title": "Rollback fixed the deployment issue",
            "summary": ["resolved after rollback"],
            "tags": ["deploy"],
            "session_id": "s1",
            "created_at": "2026-01-01T11:00:00Z",
        }
        rel, reason_code = infer_relationship(bead_a, bead_b)
        self.assertIsInstance(rel, str)
        self.assertTrue(len(rel) > 0)
        self.assertIsInstance(reason_code, str)
        # The two beads share tags and session — classifier should produce something specific
        canonical = {"supports", "leads_to", "causes", "associated_with", "precedes", "follows"}
        self.assertIn(rel, canonical)

    def test_classifier_fills_provenance(self):
        """When classifier fills the relationship, provenance should be preview_classifier."""
        with tempfile.TemporaryDirectory() as td:
            s, b1, b2 = self._setup_store_with_two_beads(td)

            from core_memory.association.crawler_contract import apply_crawler_updates, merge_crawler_updates
            apply_crawler_updates(
                td,
                "sess-1",
                {
                    "associations": [
                        {
                            "source_bead_id": b1,
                            "target_bead_id": b2,
                            "relationship": "",
                            "reason_text": "bead pairs are related",
                            "confidence": 0.7,
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )
            merge_crawler_updates(td, "sess-1")
            idx_path = Path(td) / ".beads" / "index.json"
            idx = json.loads(idx_path.read_text())
            assocs = [a for a in idx.get("associations", [])
                      if a.get("source_bead") == b1 and a.get("target_bead") == b2]
            self.assertTrue(len(assocs) > 0)
            # All classifier-filled associations should record preview_classifier provenance
            self.assertTrue(any(a.get("provenance") == "preview_classifier" for a in assocs), assocs)


if __name__ == "__main__":
    unittest.main()
