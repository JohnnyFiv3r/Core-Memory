"""F-RW2 acceptance tests: selection score with reinforcement-weighted decay.

Verifies:
1. Old design_principle recalled last week beats a 5-day-old context bead.
2. Old design_principle never touched loses to a 5-day-old lesson.
3. Type diversity holds when score-ordered fill would exclude a type.
4. Forced latest substantive skips lifecycle beads.
5. Selection policy string updated from FIFO to score-weighted.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core_memory.persistence.store import MemoryStore
from core_memory.policy.promotion import (
    BASE_HALF_LIFE_DAYS,
    DIVERSITY_REQUIRED_TYPES,
    TYPE_DURABILITY_MULTIPLIERS,
    _days_since_last_touch,
    compute_selection_score,
)
from core_memory.write_pipeline.rolling_window import (
    _ensure_type_diversity,
    _forced_latest_substantive,
    _is_lifecycle_bead,
    _select_beads_for_budget,
    build_rolling_surface,
)


NOW = datetime.now(timezone.utc)


class TestSelectionScoreDecay(unittest.TestCase):
    """Core decay math tests from the fix plan."""

    def test_old_design_principle_recalled_beats_young_context(self):
        """60-day-old design_principle recalled last week beats 5-day-old context."""
        dp = {
            "id": "dp1", "type": "design_principle",
            "created_at": (NOW - timedelta(days=60)).isoformat(),
            "last_recalled_at": (NOW - timedelta(days=7)).isoformat(),
        }
        ctx = {
            "id": "ctx1", "type": "context",
            "created_at": (NOW - timedelta(days=5)).isoformat(),
        }
        s_dp, _ = compute_selection_score({}, dp, now=NOW)
        s_ctx, _ = compute_selection_score({}, ctx, now=NOW)
        self.assertGreater(s_dp, s_ctx)

    def test_old_untouched_design_principle_loses_to_young_lesson(self):
        """60-day-old design_principle never touched loses to 5-day-old lesson."""
        dp = {
            "id": "dp1", "type": "design_principle",
            "created_at": (NOW - timedelta(days=60)).isoformat(),
        }
        lesson = {
            "id": "l1", "type": "lesson",
            "created_at": (NOW - timedelta(days=5)).isoformat(),
        }
        s_dp, _ = compute_selection_score({}, dp, now=NOW)
        s_lesson, _ = compute_selection_score({}, lesson, now=NOW)
        self.assertLess(s_dp, s_lesson)

    def test_recall_resets_decay_clock(self):
        """A bead recalled yesterday should barely decay."""
        bead = {
            "id": "b1", "type": "decision",
            "created_at": (NOW - timedelta(days=90)).isoformat(),
            "last_recalled_at": (NOW - timedelta(days=1)).isoformat(),
        }
        days = _days_since_last_touch(bead, now=NOW)
        self.assertAlmostEqual(days, 1.0, places=0)

    def test_reinforcement_resets_decay_clock(self):
        bead = {
            "id": "b1", "type": "decision",
            "created_at": (NOW - timedelta(days=90)).isoformat(),
            "last_reinforced_at": (NOW - timedelta(days=2)).isoformat(),
        }
        days = _days_since_last_touch(bead, now=NOW)
        self.assertAlmostEqual(days, 2.0, places=0)

    def test_association_resets_decay_clock(self):
        bead = {
            "id": "b1", "type": "decision",
            "created_at": (NOW - timedelta(days=90)).isoformat(),
            "last_association_added_at": (NOW - timedelta(days=3)).isoformat(),
        }
        days = _days_since_last_touch(bead, now=NOW)
        self.assertAlmostEqual(days, 3.0, places=0)


class TestTypeDurabilityMultipliers(unittest.TestCase):
    """Multipliers match the fix plan spec."""

    def test_design_principle_multiplier(self):
        self.assertEqual(TYPE_DURABILITY_MULTIPLIERS["design_principle"], 4.0)

    def test_decision_multiplier(self):
        self.assertEqual(TYPE_DURABILITY_MULTIPLIERS["decision"], 2.0)

    def test_context_multiplier(self):
        self.assertEqual(TYPE_DURABILITY_MULTIPLIERS["context"], 1.0)

    def test_base_half_life(self):
        self.assertEqual(BASE_HALF_LIFE_DAYS, 14.0)

    def test_unknown_type_defaults_to_1(self):
        self.assertEqual(TYPE_DURABILITY_MULTIPLIERS.get("unknown_type", 1.0), 1.0)


class TestForcedLatestSubstantive(unittest.TestCase):
    """Forced latest skips lifecycle beads."""

    def test_skips_checkpoint(self):
        beads = [
            {"id": "ck1", "type": "checkpoint", "summary": ["auto"]},
            {"id": "d1", "type": "decision", "summary": ["chose X"]},
        ]
        result = _forced_latest_substantive(beads)
        self.assertEqual(result["id"], "d1")

    def test_skips_session_start(self):
        beads = [
            {"id": "ss1", "type": "session_start", "summary": ["start"]},
            {"id": "l1", "type": "lesson", "summary": ["learned Y"]},
        ]
        result = _forced_latest_substantive(beads)
        self.assertEqual(result["id"], "l1")

    def test_skips_empty_context(self):
        beads = [
            {"id": "c1", "type": "context", "summary": []},
            {"id": "d1", "type": "decision", "summary": ["chose X"]},
        ]
        result = _forced_latest_substantive(beads)
        self.assertEqual(result["id"], "d1")

    def test_returns_none_when_all_lifecycle(self):
        beads = [
            {"id": "ck1", "type": "checkpoint", "summary": ["auto"]},
            {"id": "ss1", "type": "session_start", "summary": ["start"]},
        ]
        result = _forced_latest_substantive(beads)
        self.assertIsNone(result)

    def test_decision_is_not_lifecycle(self):
        self.assertFalse(_is_lifecycle_bead({"type": "decision", "summary": ["x"]}))

    def test_context_with_content_is_not_lifecycle(self):
        self.assertFalse(_is_lifecycle_bead({"type": "context", "summary": ["real content"]}))


class TestTypeDiversity(unittest.TestCase):
    """Type diversity pass guarantees decision/lesson/outcome when available."""

    def test_diversity_swaps_in_missing_type(self):
        included = [
            {"id": "d1", "type": "decision"},
            {"id": "d2", "type": "decision"},
            {"id": "d3", "type": "decision"},
        ]
        scored_remaining = [
            ({"id": "l1", "type": "lesson"}, 0.5, {}),
            ({"id": "o1", "type": "outcome"}, 0.4, {}),
        ]
        result = _ensure_type_diversity(included, scored_remaining)
        result_types = {str(b.get("type") or "") for b in result}
        self.assertIn("lesson", result_types)

    def test_diversity_noop_when_all_present(self):
        included = [
            {"id": "d1", "type": "decision"},
            {"id": "l1", "type": "lesson"},
            {"id": "o1", "type": "outcome"},
        ]
        result = _ensure_type_diversity(included, [])
        self.assertEqual(len(result), 3)

    def test_required_types_match_spec(self):
        self.assertEqual(DIVERSITY_REQUIRED_TYPES, {"decision", "lesson", "outcome"})


class TestSelectionPolicyString(unittest.TestCase):
    """Selection policy metadata reflects the new algorithm."""

    def test_policy_string_updated(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["one"], session_id="s1", source_turn_ids=["t1"])
            _text, meta, _incl, _excl = build_rolling_surface(td, token_budget=3000, max_beads=80)
            self.assertEqual(meta["selection_policy"], "score_weighted_with_budget_forced_latest_substantive")


class TestEndToEndSelection(unittest.TestCase):
    """Integration: build_rolling_surface uses score-weighted selection."""

    def test_builds_successfully(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Decision A", summary=["chose X"], session_id="s1", source_turn_ids=["t1"])
            s.add_bead(type="lesson", title="Lesson B", summary=["learned Y"], session_id="s1", source_turn_ids=["t2"])
            s.add_bead(type="context", title="Context C", summary=["background"], session_id="s1", source_turn_ids=["t3"])
            text, meta, included_ids, excluded_ids = build_rolling_surface(td, token_budget=3000, max_beads=80)
            self.assertGreater(len(included_ids), 0)
            self.assertIn("token_estimate", meta)


if __name__ == "__main__":
    unittest.main()
