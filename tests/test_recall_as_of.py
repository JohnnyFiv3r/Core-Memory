"""Tests for the as_of temporal filter on recall()."""
import math
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core_memory.retrieval.agent import _filter_evidence_by_as_of, recall, _EFFORT_DEFAULTS
from core_memory.retrieval.contracts import EvidenceItem, RecallResult


class TestFilterEvidenceByAsOf(unittest.TestCase):
    def _item(self, bead_id: str, created_at: str) -> EvidenceItem:
        return EvidenceItem(bead_id=bead_id, metadata={"created_at": created_at})

    def test_excludes_bead_created_after_as_of(self):
        items = [
            self._item("old", "2026-01-01T00:00:00Z"),
            self._item("new", "2026-06-01T00:00:00Z"),
        ]
        result = _filter_evidence_by_as_of(items, "2026-03-01T00:00:00Z")
        self.assertEqual(["old"], [i.bead_id for i in result])

    def test_includes_bead_created_exactly_at_boundary(self):
        items = [self._item("exact", "2026-03-01T00:00:00Z")]
        result = _filter_evidence_by_as_of(items, "2026-03-01T00:00:00Z")
        self.assertEqual(["exact"], [i.bead_id for i in result])

    def test_passes_through_items_without_created_at(self):
        item = EvidenceItem(bead_id="no-ts", metadata={})
        result = _filter_evidence_by_as_of([item], "2026-01-01T00:00:00Z")
        self.assertEqual(["no-ts"], [i.bead_id for i in result])

    def test_invalid_as_of_passes_through_all(self):
        items = [self._item("x", "2026-06-01T00:00:00Z")]
        result = _filter_evidence_by_as_of(items, "not-a-date")
        self.assertEqual(["x"], [i.bead_id for i in result])


class TestRecallAsOfValidation(unittest.TestCase):
    def test_bad_as_of_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            recall("anything", as_of="not-a-timestamp", root="/tmp/nonexistent")
        self.assertIn("ISO 8601", str(ctx.exception))

    def test_future_as_of_is_accepted_as_valid_timestamp(self):
        # Valid ISO timestamp — should not raise at validation time even if far future.
        # The actual recall will fail due to missing root, which is acceptable here.
        try:
            recall("anything", as_of="2099-01-01T00:00:00Z", root="/tmp/nonexistent")
        except ValueError as exc:
            self.fail(f"Valid future as_of raised ValueError: {exc}")
        except Exception:
            pass  # expected — /tmp/nonexistent is not a real memory root


class TestRecallAsOfResultField(unittest.TestCase):
    """result.as_of and result.metadata['as_of'] are both populated."""

    def _make_raw(self):
        return {"ok": True, "results": [], "answer": None}

    def test_result_as_of_field_set(self):
        raw = self._make_raw()
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), \
             patch("core_memory.retrieval.agent._enrich_recall_state"), \
             patch("core_memory.retrieval.agent._attach_conflict_reviews"):
            result = recall("query", as_of="2026-03-01T00:00:00Z", root="/tmp/x")
        self.assertEqual("2026-03-01T00:00:00Z", result.as_of)

    def test_result_metadata_as_of_set(self):
        raw = self._make_raw()
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), \
             patch("core_memory.retrieval.agent._enrich_recall_state"), \
             patch("core_memory.retrieval.agent._attach_conflict_reviews"):
            result = recall("query", as_of="2026-03-01T00:00:00Z", root="/tmp/x")
        self.assertEqual("2026-03-01T00:00:00Z", result.metadata.get("as_of"))

    def test_result_as_of_none_when_not_set(self):
        raw = self._make_raw()
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw), \
             patch("core_memory.retrieval.agent._enrich_recall_state"), \
             patch("core_memory.retrieval.agent._attach_conflict_reviews"):
            result = recall("query", root="/tmp/x")
        self.assertIsNone(result.as_of)


class TestRecallAsOfKInflation(unittest.TestCase):
    """When as_of is set, k sent to memory_execute is inflated by 1.5x."""

    def _captured_k(self, effort: str, explicit_k=None) -> int:
        captured = {}
        raw = {"ok": True, "results": [], "answer": None}

        def fake_execute(*, request, root, explain):
            captured["k"] = request.get("k")
            return raw

        kwargs = {"as_of": "2026-01-01T00:00:00Z", "effort": effort, "root": "/tmp/x"}
        if explicit_k is not None:
            kwargs["k"] = explicit_k

        with patch("core_memory.retrieval.agent.memory_execute", side_effect=fake_execute), \
             patch("core_memory.retrieval.agent._enrich_recall_state"), \
             patch("core_memory.retrieval.agent._attach_conflict_reviews"):
            recall("query", **kwargs)
        return captured["k"]

    def test_low_effort_k_inflated(self):
        base = _EFFORT_DEFAULTS["low"]["k"]
        expected = min(int(base * 1.5 + 0.5), 50)
        self.assertEqual(expected, self._captured_k("low"))

    def test_medium_effort_k_inflated(self):
        base = _EFFORT_DEFAULTS["medium"]["k"]
        expected = min(int(base * 1.5 + 0.5), 50)
        self.assertEqual(expected, self._captured_k("medium"))

    def test_high_effort_k_inflated(self):
        base = _EFFORT_DEFAULTS["high"]["k"]
        expected = min(int(base * 1.5 + 0.5), 50)
        self.assertEqual(expected, self._captured_k("high"))

    def test_explicit_k_is_inflated(self):
        expected = min(int(6 * 1.5 + 0.5), 50)
        self.assertEqual(expected, self._captured_k("medium", explicit_k=6))

    def test_k_capped_at_50(self):
        # A very large explicit k should not exceed 50 after inflation.
        result_k = self._captured_k("high", explicit_k=40)
        self.assertLessEqual(result_k, 50)

    def test_k_not_inflated_without_as_of(self):
        captured = {}
        raw = {"ok": True, "results": [], "answer": None}

        def fake_execute(*, request, root, explain):
            captured["k"] = request.get("k")
            return raw

        with patch("core_memory.retrieval.agent.memory_execute", side_effect=fake_execute), \
             patch("core_memory.retrieval.agent._enrich_recall_state"), \
             patch("core_memory.retrieval.agent._attach_conflict_reviews"):
            recall("query", effort="medium", root="/tmp/x")
        self.assertEqual(_EFFORT_DEFAULTS["medium"]["k"], captured["k"])


if __name__ == "__main__":
    unittest.main()
