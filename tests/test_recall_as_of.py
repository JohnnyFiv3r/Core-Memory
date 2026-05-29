"""Tests for the as_of temporal filter on recall()."""
import unittest

from core_memory.retrieval.agent import _filter_evidence_by_as_of, recall
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


if __name__ == "__main__":
    unittest.main()
