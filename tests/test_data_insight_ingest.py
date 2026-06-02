"""Tests for the external data insight ingest path (#16).

Covers:
- Valid row → bead created with correct type and fields
- Row missing required field → ValueError raised, no bead written
- BeadType enum includes data_insight
- ingest_data_insight_row() calls emit_turn_finalized (never writes bead directly)
- Duplicate source_record_id is safe (idempotent via turn_id)
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core_memory.runtime.ingest.data_insight import _REQUIRED_FIELDS, ingest_data_insight_row


def _valid_row(**overrides) -> dict:
    base = {
        "id": "ph-row-001",
        "source_table": "pipeline_metrics",
        "as_of_timestamp": "2026-05-29T10:00:00Z",
        "entity_refs": ["Acme Corp"],
        "attribute_tags": ["cogs_anomaly", "28pct_above_baseline"],
        "title": "COGS up 28% for Acme Corp in May",
        "content": "COGS for Acme Corp reached $142k in May 2026, 28% above baseline.",
    }
    base.update(overrides)
    return base


class TestBeadTypeEnum(unittest.TestCase):
    def test_data_insight_in_enum(self):
        from core_memory.schema.models import BeadType
        self.assertEqual(BeadType.DATA_INSIGHT.value, "data_insight")


class TestRequiredFieldValidation(unittest.TestCase):
    def test_missing_id_raises(self):
        row = _valid_row()
        del row["id"]
        with self.assertRaises(ValueError) as ctx:
            ingest_data_insight_row("/tmp/root", "sess", row)
        self.assertIn("id", str(ctx.exception))

    def test_missing_source_table_raises(self):
        row = _valid_row(source_table="")
        with self.assertRaises(ValueError) as ctx:
            ingest_data_insight_row("/tmp/root", "sess", row)
        self.assertIn("source_table", str(ctx.exception))

    def test_missing_content_raises(self):
        row = _valid_row()
        del row["content"]
        with self.assertRaises(ValueError):
            ingest_data_insight_row("/tmp/root", "sess", row)

    def test_all_required_fields_covered(self):
        expected = {"id", "source_table", "as_of_timestamp", "entity_refs", "attribute_tags", "title", "content"}
        self.assertEqual(set(_REQUIRED_FIELDS), expected)


class TestIngestDataInsightRow(unittest.TestCase):
    def _run_with_mock_emit(self, row: dict, session_id: str = "sess-test", bead_id: str = "bead-xyz") -> dict:
        mock_result = {"ok": True, "bead_id": bead_id}
        with patch("core_memory.runtime.ingest.data_insight.emit_turn_finalized", return_value=mock_result) as mock_emit:
            result = ingest_data_insight_row("/tmp/root", session_id, row)
            return result, mock_emit

    def test_returns_ok_and_bead_id(self):
        result, _ = self._run_with_mock_emit(_valid_row())
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("bead_id"), "bead-xyz")

    def test_turn_id_encodes_source_record_id(self):
        result, mock_emit = self._run_with_mock_emit(_valid_row(id="ph-row-777"))
        self.assertEqual(result.get("turn_id"), "data-insight-ph-row-777")
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["turn_id"], "data-insight-ph-row-777")

    def test_origin_is_pipehouse(self):
        _, mock_emit = self._run_with_mock_emit(_valid_row())
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["origin"], "pipehouse")

    def test_turn_metadata_has_correct_type(self):
        _, mock_emit = self._run_with_mock_emit(_valid_row())
        call_kwargs = mock_emit.call_args[1]
        turn = call_kwargs["turns"][0]
        self.assertEqual(turn["metadata"]["type"], "data_insight")
        self.assertEqual(turn["metadata"]["source_system"], "pipehouse")

    def test_turn_metadata_has_source_record_id(self):
        _, mock_emit = self._run_with_mock_emit(_valid_row(id="rec-abc"))
        call_kwargs = mock_emit.call_args[1]
        meta = call_kwargs["turns"][0]["metadata"]
        self.assertEqual(meta["source_record_id"], "rec-abc")
        self.assertEqual(meta["links"]["external_source_id"], "rec-abc")

    def test_title_truncated_to_120_chars(self):
        long_title = "X" * 200
        _, mock_emit = self._run_with_mock_emit(_valid_row(title=long_title))
        meta = mock_emit.call_args[1]["turns"][0]["metadata"]
        self.assertLessEqual(len(meta["title"]), 120)

    def test_unifying_id_in_links_when_provided(self):
        row = _valid_row(core_memory_unifying_id="meeting_2026-05-29_vendor-review")
        _, mock_emit = self._run_with_mock_emit(row)
        links = mock_emit.call_args[1]["turns"][0]["metadata"]["links"]
        self.assertEqual(links["core_memory_unifying_id"], "meeting_2026-05-29_vendor-review")

    def test_no_unifying_id_key_when_absent(self):
        _, mock_emit = self._run_with_mock_emit(_valid_row())
        links = mock_emit.call_args[1]["turns"][0]["metadata"]["links"]
        self.assertNotIn("core_memory_unifying_id", links)

    def test_optional_confidence_passed_through(self):
        row = _valid_row(confidence=0.75)
        _, mock_emit = self._run_with_mock_emit(row)
        meta = mock_emit.call_args[1]["turns"][0]["metadata"]
        self.assertAlmostEqual(meta["confidence"], 0.75)

    def test_emit_called_exactly_once(self):
        _, mock_emit = self._run_with_mock_emit(_valid_row())
        mock_emit.assert_called_once()

    def test_duplicate_row_same_turn_id(self):
        """Same source row submitted twice produces same turn_id — idempotent via turn deduplication."""
        row = _valid_row(id="ph-dup-001")
        result1, _ = self._run_with_mock_emit(row)
        result2, _ = self._run_with_mock_emit(row)
        self.assertEqual(result1["turn_id"], result2["turn_id"])


class TestDataInsightNotInClassifiableTypes(unittest.TestCase):
    def test_data_insight_not_auto_classified(self):
        from core_memory.policy.bead_typing import CLASSIFIABLE_TYPES
        self.assertNotIn("data_insight", CLASSIFIABLE_TYPES)


if __name__ == "__main__":
    unittest.main()
