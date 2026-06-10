"""Tests for multi-source seeding: entity-resolved, claim-slot, and session seeds."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.agent import (
    _CLAIM_SEED_FACTOR,
    _ENTITY_SEED_FACTOR,
    _SESSION_SEED_FACTOR,
    _HOP_DECAY,
    _collect_extra_seeds,
)
from core_memory.retrieval.contracts import EvidenceItem


def _make_evidence(bead_id: str, score: float = 0.8) -> EvidenceItem:
    return EvidenceItem(bead_id=bead_id, type="event", title=bead_id, content_excerpt="", score=score, reason="vector")


def _write_index(root: Path, beads: dict, associations: list | None = None,
                 entities: dict | None = None, entity_aliases: dict | None = None) -> None:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    index: dict = {"beads": beads, "associations": associations or []}
    # Entity registry lives in the same index.json (load_entity_registry reads from it)
    if entities is not None:
        index["entities"] = entities
    if entity_aliases is not None:
        index["entity_aliases"] = entity_aliases
    (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")


class TestCollectExtraSeedsEntityResolved(unittest.TestCase):
    def _write_alice_index(self, root: Path, retrieval_eligible: bool = True) -> None:
        beads = {
            "bead-AAAAAAAAAAAA": {
                "type": "decision",
                "title": "Alice decision",
                "retrieval_eligible": retrieval_eligible,
                "entity_ids": ["ent-alice"],
                "summary": ["Alice decided something"],
            },
        }
        _write_index(root, beads,
                     entities={"ent-alice": {"label": "Alice", "aliases": ["Alice"]}},
                     entity_aliases={"alice": "ent-alice"})

    def test_entity_seed_found_when_bead_shares_entity_with_query(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_alice_index(root)
            seeds = _collect_extra_seeds(str(root), "What did Alice decide?", [])
            bead_ids = [s.bead_id for s in seeds]
            self.assertIn("bead-AAAAAAAAAAAA", bead_ids)

    def test_entity_seed_reason_tag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_alice_index(root)
            seeds = _collect_extra_seeds(str(root), "What did Alice decide?", [])
            entity_seeds = [s for s in seeds if s.reason == "entity_seed"]
            self.assertTrue(entity_seeds, "should have at least one entity_seed item")

    def test_entity_seed_score_capped_by_factor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_alice_index(root)
            seeds = _collect_extra_seeds(str(root), "What did Alice decide?", [])
            entity_seeds = [s for s in seeds if s.reason == "entity_seed"]
            self.assertTrue(entity_seeds)
            # All entity seed scores must be ≤ _ENTITY_SEED_FACTOR (max possible)
            for s in entity_seeds:
                self.assertLessEqual(float(s.score or 0), _ENTITY_SEED_FACTOR + 1e-6)

    def test_entity_seed_not_added_if_already_in_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_alice_index(root)
            existing = [_make_evidence("bead-AAAAAAAAAAAA", score=0.9)]
            seeds = _collect_extra_seeds(str(root), "What did Alice decide?", existing)
            bead_ids = [s.bead_id for s in seeds]
            self.assertNotIn("bead-AAAAAAAAAAAA", bead_ids)

    def test_non_retrieval_eligible_bead_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_alice_index(root, retrieval_eligible=False)
            seeds = _collect_extra_seeds(str(root), "What did Alice do?", [])
            bead_ids = [s.bead_id for s in seeds]
            self.assertNotIn("bead-AAAAAAAAAAAA", bead_ids)


class TestCollectExtraSeedsClaimSlot(unittest.TestCase):
    def _write_index_with_claims(self, root: Path, bead_id: str, claims: list) -> None:
        beads_dir = root / ".beads"
        beads_dir.mkdir(parents=True, exist_ok=True)
        index = {
            "beads": {
                bead_id: {
                    "type": "decision",
                    "title": "claim bead",
                    "retrieval_eligible": True,
                    "summary": ["some summary"],
                    "claims": claims,
                }
            },
            "associations": [],
        }
        (beads_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    def test_claim_seed_found_when_slot_overlaps_query(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_index_with_claims(root, "bead-BBBBBBBBBBBB", [
                {"slot": "budget", "subject": "project", "value": "50000", "claim_text": "project budget is 50000"},
            ])
            seeds = _collect_extra_seeds(str(root), "what is the project budget?", [])
            claim_seeds = [s for s in seeds if s.reason == "claim_seed"]
            bead_ids = [s.bead_id for s in claim_seeds]
            self.assertIn("bead-BBBBBBBBBBBB", bead_ids)

    def test_claim_seed_score_below_factor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_index_with_claims(root, "bead-BBBBBBBBBBBB", [
                {"slot": "budget", "subject": "project", "value": "50000", "claim_text": "project budget 50000"},
            ])
            seeds = _collect_extra_seeds(str(root), "project budget", [])
            claim_seeds = [s for s in seeds if s.reason == "claim_seed"]
            self.assertTrue(claim_seeds)
            for s in claim_seeds:
                self.assertLessEqual(float(s.score or 0), _CLAIM_SEED_FACTOR + 1e-6)

    def test_no_claim_seed_when_no_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_index_with_claims(root, "bead-BBBBBBBBBBBB", [
                {"slot": "budget", "subject": "project", "value": "50000", "claim_text": "project budget 50000"},
            ])
            seeds = _collect_extra_seeds(str(root), "what happened yesterday?", [])
            claim_seeds = [s for s in seeds if s.reason == "claim_seed"]
            self.assertEqual([], claim_seeds)


class TestCollectExtraSeedsSessionSeeds(unittest.TestCase):
    def test_session_seed_added_for_current_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-CCCCCCCCCCCC": {
                    "type": "event",
                    "title": "recent session bead",
                    "retrieval_eligible": True,
                    "session_id": "session-42",
                    "summary": ["something recent"],
                },
            })
            seeds = _collect_extra_seeds(str(root), "anything", [], session_id="session-42")
            session_seeds = [s for s in seeds if s.reason == "session_seed"]
            bead_ids = [s.bead_id for s in session_seeds]
            self.assertIn("bead-CCCCCCCCCCCC", bead_ids)

    def test_session_seed_not_added_for_other_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-CCCCCCCCCCCC": {
                    "type": "event",
                    "title": "other session bead",
                    "retrieval_eligible": True,
                    "session_id": "session-99",
                    "summary": ["old session"],
                },
            })
            seeds = _collect_extra_seeds(str(root), "anything", [], session_id="session-42")
            session_seeds = [s for s in seeds if s.reason == "session_seed"]
            self.assertEqual([], session_seeds)

    def test_session_seed_not_added_without_session_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-CCCCCCCCCCCC": {
                    "type": "event",
                    "title": "session bead",
                    "retrieval_eligible": True,
                    "session_id": "session-42",
                    "summary": ["some content"],
                },
            })
            seeds = _collect_extra_seeds(str(root), "anything", [])  # no session_id
            session_seeds = [s for s in seeds if s.reason == "session_seed"]
            self.assertEqual([], session_seeds)

    def test_session_seed_most_recent_score_equals_factor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-CCCCCCCCCCCC": {
                    "type": "event",
                    "title": "most recent",
                    "retrieval_eligible": True,
                    "session_id": "s1",
                    "summary": ["latest"],
                },
                "bead-AAAAAAAAAAAA": {
                    "type": "event",
                    "title": "older",
                    "retrieval_eligible": True,
                    "session_id": "s1",
                    "summary": ["earlier"],
                },
            })
            seeds = _collect_extra_seeds(str(root), "anything", [], session_id="s1")
            session_seeds = sorted([s for s in seeds if s.reason == "session_seed"], key=lambda s: -float(s.score or 0))
            self.assertTrue(session_seeds)
            # Most recent bead gets the highest session seed score
            self.assertAlmostEqual(float(session_seeds[0].score or 0), _SESSION_SEED_FACTOR, places=2)
            # Second bead gets decay applied
            if len(session_seeds) > 1:
                expected_second = round(_SESSION_SEED_FACTOR * _HOP_DECAY, 4)
                self.assertAlmostEqual(float(session_seeds[1].score or 0), expected_second, places=2)

    def test_session_seed_not_added_if_already_in_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-CCCCCCCCCCCC": {
                    "type": "event",
                    "title": "session bead",
                    "retrieval_eligible": True,
                    "session_id": "s1",
                    "summary": ["content"],
                },
            })
            existing = [_make_evidence("bead-CCCCCCCCCCCC", score=0.9)]
            seeds = _collect_extra_seeds(str(root), "anything", existing, session_id="s1")
            session_seeds = [s for s in seeds if s.reason == "session_seed"]
            self.assertEqual([], session_seeds)


class TestCollectExtraSeedsEmptyIndex(unittest.TestCase):
    def test_returns_empty_on_missing_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".beads").mkdir()
            seeds = _collect_extra_seeds(str(root), "anything", [], session_id="s1")
            self.assertEqual([], seeds)

    def test_returns_empty_on_empty_beads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {})
            seeds = _collect_extra_seeds(str(root), "anything", [], session_id="s1")
            self.assertEqual([], seeds)


class TestCollectExtraSeedsDeduplicate(unittest.TestCase):
    def test_entity_seed_and_session_seed_same_bead_deduplicated(self):
        """A bead matching both entity and session criteria should appear once."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_index(root, {
                "bead-AAAAAAAAAAAA": {
                    "type": "decision",
                    "title": "Alice session bead",
                    "retrieval_eligible": True,
                    "entity_ids": ["ent-alice"],
                    "session_id": "s1",
                    "summary": ["Alice did something this session"],
                },
            },
            entities={"ent-alice": {"label": "Alice", "aliases": ["Alice"]}},
            entity_aliases={"alice": "ent-alice"},
            )
            seeds = _collect_extra_seeds(str(root), "What did Alice do?", [], session_id="s1")
            bead_ids = [s.bead_id for s in seeds]
            # Should appear exactly once even though it matches both entity and session criteria
            self.assertEqual(1, bead_ids.count("bead-AAAAAAAAAAAA"))


if __name__ == "__main__":
    unittest.main()
