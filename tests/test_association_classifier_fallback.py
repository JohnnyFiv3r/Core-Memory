"""Preview classification may propose pairs but cannot author canonical relations."""
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

    def test_empty_relationship_is_quarantined_for_agent_judge(self):
        """An association without an agent relation is never queued as canonical."""
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
            self.assertEqual(result.get("associations_appended"), 0, result)
            self.assertEqual(result.get("associations_quarantined"), 1, result)

    def test_none_relationship_is_quarantined_for_agent_judge(self):
        """A null relation also requires a full-schema agent repair/judge."""
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
            self.assertEqual(result.get("associations_appended"), 0, result)
            self.assertEqual(result.get("associations_quarantined"), 1, result)

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
                            "relationship": "caused_by",
                            "reason_text": "explicit agent decision",
                            "confidence": 0.9,
                        }
                    ]
                },
                visible_bead_ids=[b1, b2],
            )
            self.assertEqual(result.get("associations_appended"), 1, result)
            # Verify the stored relationship is the agent-supplied one
            idx_path = Path(td) / ".beads" / "index.json"
            from core_memory.association.crawler_contract import merge_crawler_updates
            merge_crawler_updates(td, "sess-1")
            idx = json.loads(idx_path.read_text())
            assocs = [a for a in idx.get("associations", [])
                      if a.get("source_bead") == b1 and a.get("target_bead") == b2]
            self.assertTrue(any(a.get("relationship") == "caused_by" for a in assocs), assocs)

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
        canonical = {"supports", "led_to", "caused_by", "associated_with", "precedes", "follows"}
        self.assertIn(rel, canonical)

    def test_missing_relationship_quarantine_has_repair_provenance(self):
        """A preview classifier cannot silently create a canonical association."""
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
            self.assertEqual([], assocs)
            quarantine_path = Path(td) / ".beads" / "events" / "association-quarantine.jsonl"
            quarantine_rows = [json.loads(line) for line in quarantine_path.read_text().splitlines() if line.strip()]
            self.assertTrue(
                any(
                    "missing_relationship_requires_agent_judge" in row.get("reasons", [])
                    and "preview_classifier_cannot_author_canonical_relation" in row.get("warnings", [])
                    for row in quarantine_rows
                ),
                quarantine_rows,
            )


if __name__ == "__main__":
    unittest.main()
