"""F-RW4 acceptance tests: dedup pass in rolling window.

Verifies:
1. Identical (title, first_summary_line) pair dedupes correctly.
2. Highest promotion_score bead wins.
3. Same title different summary does NOT dedupe.
4. Same summary different title does NOT dedupe.
5. Dedup hits are logged.
"""

import logging
import unittest
from typing import Any
from unittest.mock import patch

from core_memory.write_pipeline.rolling_window import _dedup_beads, _dedup_key


def _bead(bid: str, title: str, summary: list[str], btype: str = "decision") -> dict[str, Any]:
    return {"id": bid, "type": btype, "title": title, "summary": summary}


class TestDedupKey(unittest.TestCase):
    """Dedup key is (title + first_summary_line), case-insensitive."""

    def test_same_title_same_summary(self):
        a = _bead("a", "Redis decision", ["Raised pool size"])
        b = _bead("b", "Redis decision", ["Raised pool size"])
        self.assertEqual(_dedup_key(a), _dedup_key(b))

    def test_case_insensitive(self):
        a = _bead("a", "Redis Decision", ["Raised pool size"])
        b = _bead("b", "redis decision", ["raised pool size"])
        self.assertEqual(_dedup_key(a), _dedup_key(b))

    def test_different_summary_different_key(self):
        a = _bead("a", "Redis decision", ["Raised pool size"])
        b = _bead("b", "Redis decision", ["Switched to connection pooling"])
        self.assertNotEqual(_dedup_key(a), _dedup_key(b))

    def test_different_title_different_key(self):
        a = _bead("a", "Redis decision", ["Raised pool size"])
        b = _bead("b", "Postgres decision", ["Raised pool size"])
        self.assertNotEqual(_dedup_key(a), _dedup_key(b))

    def test_empty_summary(self):
        a = _bead("a", "Title", [])
        b = _bead("b", "Title", [])
        self.assertEqual(_dedup_key(a), _dedup_key(b))

    def test_multi_line_summary_uses_first_only(self):
        a = _bead("a", "Title", ["first line", "second line differs"])
        b = _bead("b", "Title", ["first line", "totally different"])
        self.assertEqual(_dedup_key(a), _dedup_key(b))


class TestDedupBeads(unittest.TestCase):
    """Dedup removes exact duplicates, keeping highest promotion_score."""

    def test_exact_duplicate_dedupes(self):
        beads = [
            _bead("a", "Redis decision", ["Raised pool size"], "decision"),
            _bead("b", "Redis decision", ["Raised pool size"], "decision"),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 1)
        self.assertEqual(len(dropped), 1)

    def test_higher_score_wins(self):
        # evidence type has lower prior (0.58) than decision (0.66)
        beads = [
            _bead("low", "Redis decision", ["Raised pool size"], "evidence"),
            _bead("high", "Redis decision", ["Raised pool size"], "decision"),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 1)
        winner = deduped[0]
        self.assertEqual(winner["id"], "high")

    def test_no_false_positives_different_summary(self):
        beads = [
            _bead("a", "Redis decision", ["Raised pool size"]),
            _bead("b", "Redis decision", ["Switched to connection pooling"]),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 2)
        self.assertEqual(len(dropped), 0)

    def test_no_false_positives_different_title(self):
        beads = [
            _bead("a", "Redis decision", ["Raised pool size"]),
            _bead("b", "Postgres decision", ["Raised pool size"]),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 2)
        self.assertEqual(len(dropped), 0)

    def test_three_way_dedup_keeps_one(self):
        beads = [
            _bead("a", "Same title", ["Same summary"], "context"),
            _bead("b", "Same title", ["Same summary"], "decision"),
            _bead("c", "Same title", ["Same summary"], "evidence"),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 1)
        self.assertEqual(len(dropped), 2)
        # decision (0.66) beats evidence (0.58) beats context (0.35)
        self.assertEqual(deduped[0]["id"], "b")

    def test_empty_input(self):
        deduped, dropped = _dedup_beads([], {})
        self.assertEqual(deduped, [])
        self.assertEqual(dropped, [])

    def test_no_duplicates_returns_all(self):
        beads = [
            _bead("a", "Title A", ["Summary A"]),
            _bead("b", "Title B", ["Summary B"]),
            _bead("c", "Title C", ["Summary C"]),
        ]
        deduped, dropped = _dedup_beads(beads, {})
        self.assertEqual(len(deduped), 3)
        self.assertEqual(len(dropped), 0)


class TestDedupLogging(unittest.TestCase):
    """Dedup hits are logged as quality signals."""

    def test_dedup_logs_hit(self):
        beads = [
            _bead("a", "Same", ["Same"]),
            _bead("b", "Same", ["Same"]),
        ]
        with self.assertLogs("core_memory.write_pipeline.rolling_window", level="INFO") as cm:
            _dedup_beads(beads, {})
        self.assertTrue(any("dedup" in msg for msg in cm.output))
        self.assertTrue(any("winner" in msg for msg in cm.output))

    def test_no_dedup_no_log(self):
        beads = [
            _bead("a", "Title A", ["Summary A"]),
            _bead("b", "Title B", ["Summary B"]),
        ]
        # Should not produce any log output at INFO level from this module
        rw_logger = logging.getLogger("core_memory.write_pipeline.rolling_window")
        with patch.object(rw_logger, "info") as mock_info:
            _dedup_beads(beads, {})
        mock_info.assert_not_called()


class TestSummaryTruncationLimits(unittest.TestCase):
    """F-RW5: Per-type summary truncation length."""

    def test_decision_gets_five_lines(self):
        from core_memory.policy.promotion import summary_truncation_limit
        self.assertEqual(summary_truncation_limit("decision"), 5)

    def test_context_gets_one_line(self):
        from core_memory.policy.promotion import summary_truncation_limit
        self.assertEqual(summary_truncation_limit("context"), 1)

    def test_design_principle_gets_five_lines(self):
        from core_memory.policy.promotion import summary_truncation_limit
        self.assertEqual(summary_truncation_limit("design_principle"), 5)

    def test_unknown_type_gets_default(self):
        from core_memory.policy.promotion import summary_truncation_limit, DEFAULT_SUMMARY_TRUNCATION
        self.assertEqual(summary_truncation_limit("unknown_type"), DEFAULT_SUMMARY_TRUNCATION)
        self.assertEqual(DEFAULT_SUMMARY_TRUNCATION, 2)

    def test_lesson_gets_four_lines(self):
        from core_memory.policy.promotion import summary_truncation_limit
        self.assertEqual(summary_truncation_limit("lesson"), 4)


if __name__ == "__main__":
    unittest.main()
