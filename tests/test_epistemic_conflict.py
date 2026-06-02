"""Tests for #14 — Contradiction pressure and epistemic uncertainty.

Covers:
- compute_epistemic_conflict_score formula
- conflict_score_for_pair from claim dicts
- ConflictItem dataclass (contracts.py)
- RecallResult.conflicts field round-trips
- _conflicts_for_result populates conflicts when claim status is 'conflict'
- recall() populates RecallResult.conflicts (integration)
- enqueue_contradiction_pressure_candidates emits candidates above threshold
- No candidate emitted when score is below threshold
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


# ── Scoring ───────────────────────────────────────────────────────────────────

class TestComputeEpistemicConflictScore(unittest.TestCase):
    from core_memory.claim.epistemic import compute_epistemic_conflict_score

    def _score(self, time_days, seq_gap):
        from core_memory.claim.epistemic import compute_epistemic_conflict_score
        return compute_epistemic_conflict_score({}, {}, seq_gap, time_days)

    def test_zero_inputs_returns_zero(self):
        self.assertEqual(self._score(0.0, 0), 0.0)

    def test_six_month_conflict_maxes_time_component(self):
        # 180+ days → time_component = 1.0; seq_gap=0 → seq_component=0.0
        score = self._score(180.0, 0)
        self.assertAlmostEqual(score, 0.6, places=5)

    def test_ten_seq_gap_maxes_seq_component(self):
        # time=0 → 0.0; seq_gap=10 → seq_component=1.0
        score = self._score(0.0, 10)
        self.assertAlmostEqual(score, 0.4, places=5)

    def test_full_score(self):
        # 180 days + seq_gap 10 → 0.6 + 0.4 = 1.0
        score = self._score(180.0, 10)
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_clamped_above_one(self):
        score = self._score(365.0, 100)
        self.assertLessEqual(score, 1.0)

    def test_partial_time_partial_seq(self):
        # 90 days → 0.5 time; seq_gap=5 → 0.5 seq
        score = self._score(90.0, 5)
        # 0.6*0.5 + 0.4*0.5 = 0.5
        self.assertAlmostEqual(score, 0.5, places=5)

    def test_score_in_unit_interval(self):
        for td, sg in [(0, 0), (30, 3), (90, 7), (200, 15)]:
            s = self._score(float(td), sg)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


class TestConflictScoreForPair(unittest.TestCase):
    def test_claims_with_timestamps(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        # Two claims 180 days apart, no chain_seq
        a = {"id": "c1", "created_at": "2026-01-01T00:00:00Z", "chain_seq": None}
        b = {"id": "c2", "created_at": "2026-07-01T00:00:00Z", "chain_seq": None}
        score = conflict_score_for_pair(a, b)
        # ~181 days → time_component ≈ 1.0; seq_gap=0 → seq_component=0.0
        self.assertGreater(score, 0.5)

    def test_claims_same_day_no_seq(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"id": "c1", "created_at": "2026-01-01T00:00:00Z", "chain_seq": "0"}
        b = {"id": "c2", "created_at": "2026-01-01T00:00:00Z", "chain_seq": "0"}
        score = conflict_score_for_pair(a, b)
        self.assertEqual(score, 0.0)

    def test_claims_with_chain_seq_gap(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"id": "c1", "chain_seq": "1"}
        b = {"id": "c2", "chain_seq": "11"}  # gap of 10
        score = conflict_score_for_pair(a, b)
        # time=0 → 0.0; seq_gap=10 → 0.4
        self.assertAlmostEqual(score, 0.4, places=5)

    def test_missing_timestamps_no_error(self):
        from core_memory.claim.epistemic import conflict_score_for_pair
        a = {"id": "c1"}
        b = {"id": "c2"}
        score = conflict_score_for_pair(a, b)
        self.assertIsInstance(score, float)


# ── ConflictItem dataclass ────────────────────────────────────────────────────

class TestConflictItem(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        from core_memory.retrieval.contracts import ConflictItem
        item = ConflictItem(
            subject="db",
            slot="engine",
            claim_a_id="c1",
            claim_b_id="c2",
            epistemic_conflict_score=0.72,
            conflict_since="2026-01-01T00:00:00Z",
            chain_seq_gap=3,
        )
        d = item.to_dict()
        self.assertEqual(d["subject"], "db")
        self.assertAlmostEqual(d["epistemic_conflict_score"], 0.72)

        item2 = ConflictItem.from_dict(d)
        self.assertEqual(item2.subject, "db")
        self.assertEqual(item2.chain_seq_gap, 3)

    def test_default_fields(self):
        from core_memory.retrieval.contracts import ConflictItem
        item = ConflictItem(subject="x", slot="y", claim_a_id="", claim_b_id="", epistemic_conflict_score=0.0)
        self.assertEqual(item.conflict_since, "")
        self.assertEqual(item.chain_seq_gap, 0)
        self.assertIsInstance(item.metadata, dict)


# ── RecallResult.conflicts round-trip ─────────────────────────────────────────

class TestRecallResultConflicts(unittest.TestCase):
    def test_conflicts_field_exists_and_defaults_empty(self):
        from core_memory.retrieval.contracts import RecallResult
        r = RecallResult()
        self.assertEqual(r.conflicts, [])

    def test_to_dict_includes_conflicts(self):
        from core_memory.retrieval.contracts import ConflictItem, RecallResult
        r = RecallResult()
        r.conflicts = [ConflictItem(subject="db", slot="engine", claim_a_id="c1", claim_b_id="c2", epistemic_conflict_score=0.5)]
        d = r.to_dict()
        self.assertIn("conflicts", d)
        self.assertEqual(len(d["conflicts"]), 1)
        self.assertEqual(d["conflicts"][0]["subject"], "db")

    def test_from_dict_restores_conflicts(self):
        from core_memory.retrieval.contracts import ConflictItem, RecallResult
        raw = {
            "conflicts": [
                {"subject": "db", "slot": "engine", "claim_a_id": "c1", "claim_b_id": "c2",
                 "epistemic_conflict_score": 0.8, "conflict_since": "", "chain_seq_gap": 2}
            ]
        }
        r = RecallResult.from_dict(raw)
        self.assertEqual(len(r.conflicts), 1)
        self.assertIsInstance(r.conflicts[0], ConflictItem)
        self.assertAlmostEqual(r.conflicts[0].epistemic_conflict_score, 0.8)


# ── _conflicts_for_result ────────────────────────────────────────────────────

class TestConflictsForResult(unittest.TestCase):
    """Tests that _conflicts_for_result populates ConflictItems from evidence beads."""

    def _make_result_with_bead(self, bead_id: str, subject: str, slot: str):
        from core_memory.retrieval.contracts import EvidenceItem, RecallResult
        r = RecallResult()
        r.evidence = [EvidenceItem(bead_id=bead_id)]
        return r

    def _index_with_bead_claims(self, bead_id: str, subject: str, slot: str) -> dict:
        return {
            "beads": {
                bead_id: {
                    "claims": [{"subject": subject, "slot": slot, "id": "claim-1"}]
                }
            }
        }

    def test_no_evidence_returns_empty(self):
        from core_memory.retrieval.agent import _conflicts_for_result
        from core_memory.retrieval.contracts import RecallResult
        r = RecallResult()
        conflicts = _conflicts_for_result(r, ".", {})
        self.assertEqual(conflicts, [])

    def test_evidence_without_conflict_returns_empty(self):
        from core_memory.retrieval.agent import _conflicts_for_result
        result = self._make_result_with_bead("b1", "db", "engine")
        index = self._index_with_bead_claims("b1", "db", "engine")
        with patch("core_memory.retrieval.agent.resolve_current_state") as mock:
            mock.return_value = {"status": "active", "conflicts": [], "current_claim": {"id": "c1"}}
            conflicts = _conflicts_for_result(result, ".", index)
        self.assertEqual(conflicts, [])

    def test_evidence_with_conflict_returns_conflict_item(self):
        from core_memory.retrieval.agent import _conflicts_for_result
        from core_memory.retrieval.contracts import ConflictItem
        result = self._make_result_with_bead("b1", "db", "engine")
        index = self._index_with_bead_claims("b1", "db", "engine")
        with patch("core_memory.retrieval.agent.resolve_current_state") as mock:
            mock.return_value = {
                "status": "conflict",
                "conflicts": [
                    {"id": "c1", "created_at": "2026-01-01T00:00:00Z", "chain_seq": "1"},
                    {"id": "c2", "created_at": "2026-07-01T00:00:00Z", "chain_seq": "5"},
                ],
                "current_claim": {"id": "c1"},
            }
            conflicts = _conflicts_for_result(result, ".", index)
        self.assertEqual(len(conflicts), 1)
        self.assertIsInstance(conflicts[0], ConflictItem)
        self.assertEqual(conflicts[0].subject, "db")
        self.assertEqual(conflicts[0].slot, "engine")
        self.assertGreater(conflicts[0].epistemic_conflict_score, 0.0)

    def test_conflict_score_nonzero_for_old_conflict(self):
        from core_memory.retrieval.agent import _conflicts_for_result
        result = self._make_result_with_bead("b1", "db", "engine")
        index = self._index_with_bead_claims("b1", "db", "engine")
        with patch("core_memory.retrieval.agent.resolve_current_state") as mock:
            mock.return_value = {
                "status": "conflict",
                "conflicts": [
                    {"id": "c1", "created_at": "2025-01-01T00:00:00Z", "chain_seq": None},
                    {"id": "c2", "created_at": "2026-01-01T00:00:00Z", "chain_seq": None},
                ],
                "current_claim": None,
            }
            conflicts = _conflicts_for_result(result, ".", index)
        self.assertGreater(conflicts[0].epistemic_conflict_score, 0.5)


# ── dreamer candidate emission ─────────────────────────────────────────────────

class TestEnqueueContradictionPressureCandidates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".beads", "events"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_conflict(self, score: float):
        from core_memory.retrieval.contracts import ConflictItem
        return ConflictItem(
            subject="db",
            slot="engine",
            claim_a_id="c1",
            claim_b_id="c2",
            epistemic_conflict_score=score,
            conflict_since="2026-01-01T00:00:00Z",
            chain_seq_gap=3,
        )

    def test_above_threshold_emits_candidate(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.8)
        result = enqueue_contradiction_pressure_candidates(
            root=self.tmp, conflicts=[conflict], threshold=0.7
        )
        self.assertEqual(result["added"], 1)

    def test_below_threshold_no_candidate(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.5)
        result = enqueue_contradiction_pressure_candidates(
            root=self.tmp, conflicts=[conflict], threshold=0.7
        )
        self.assertEqual(result["added"], 0)

    def test_exact_threshold_not_emitted(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        # score == threshold: should not emit (strict greater-than)
        conflict = self._make_conflict(0.7)
        result = enqueue_contradiction_pressure_candidates(
            root=self.tmp, conflicts=[conflict], threshold=0.7
        )
        self.assertEqual(result["added"], 0)

    def test_candidate_has_correct_type(self):
        import json
        from pathlib import Path
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.85)
        enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[conflict], threshold=0.7)
        rows = json.loads((Path(self.tmp) / ".beads" / "events" / "dreamer-candidates.json").read_text())
        self.assertEqual(rows[0]["hypothesis_type"], "contradiction_pressure_candidate")
        self.assertEqual(rows[0]["proposal_family"], "contradiction")

    def test_candidate_stores_subject_and_slot(self):
        import json
        from pathlib import Path
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.9)
        enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[conflict], threshold=0.7)
        rows = json.loads((Path(self.tmp) / ".beads" / "events" / "dreamer-candidates.json").read_text())
        self.assertEqual(rows[0]["subject"], "db")
        self.assertEqual(rows[0]["slot"], "engine")

    def test_idempotent_same_conflict_not_duplicated(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.9)
        enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[conflict], threshold=0.7)
        result2 = enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[conflict], threshold=0.7)
        self.assertEqual(result2["added"], 0)

    def test_env_var_threshold(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        conflict = self._make_conflict(0.75)
        with patch.dict(os.environ, {"CORE_MEMORY_CONFLICT_REVIEW_THRESHOLD": "0.8"}):
            result = enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[conflict])
        # 0.75 < 0.8 → not emitted
        self.assertEqual(result["added"], 0)

    def test_empty_conflicts_ok(self):
        from core_memory.runtime.dreamer.candidates import enqueue_contradiction_pressure_candidates
        result = enqueue_contradiction_pressure_candidates(root=self.tmp, conflicts=[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 0)


if __name__ == "__main__":
    unittest.main()
