"""Tests for external recall fan-out (#15).

Ragie fan-out was retired before the 2026-07-19 API sunset. PipeHouse remains
the optional external recall source.
"""
from __future__ import annotations

import importlib.util
import os
import time
from typing import Any
from unittest.mock import patch

import pytest

from core_memory.config import feature_flags
from core_memory.retrieval.contracts import EvidenceItem, RecallResult
from core_memory.retrieval.fanout import fanout_recall, _normalize_scores, _parse_store_weights, _resolve_unifying_ids


def _cm_item(bead_id: str, score: float | None = 0.8) -> EvidenceItem:
    return EvidenceItem(
        bead_id=bead_id,
        type="decision",
        title=f"CM bead {bead_id}",
        content_excerpt="core memory content",
        score=score,
        source_store="core_memory",
        source_ref=bead_id,
    )


def _pipehouse_item(record_id: str, score: float | None = 0.7, unifying_id: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        bead_id="",
        type="data_insight",
        title=f"PipeHouse record {record_id}",
        content_excerpt="pipehouse insight",
        score=score,
        source_store="pipehouse",
        source_ref=record_id,
        unifying_id=unifying_id,
    )


def _external_item(ref: str, score: float | None = 0.9, unifying_id: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        bead_id="",
        type="document_chunk",
        title=f"External chunk {ref}",
        content_excerpt="external chunk content",
        score=score,
        source_store="external_archive",
        source_ref=ref,
        unifying_id=unifying_id,
    )


def _recall_result(*items: EvidenceItem) -> RecallResult:
    return RecallResult(
        answer="CM answer",
        evidence=list(items),
        status="answered",
    )


class TestRetiredRagiePath:
    def test_removed_adapter_module_is_absent(self) -> None:
        module_name = "core_memory.retrieval.adapters." + "ragie_" + "adapter"
        assert importlib.util.find_spec(module_name) is None

    def test_removed_api_key_flag_is_absent(self) -> None:
        flag_name = "external_" + "ragie_api_key"
        assert not hasattr(feature_flags, flag_name)

    def test_removed_api_key_env_does_not_trigger_fanout(self) -> None:
        env_name = "CORE_MEMORY_" + "RAGIE_API_KEY"
        cm_result = _recall_result(_cm_item("b1"))
        with patch.dict(os.environ, {env_name: "ignored"}, clear=False):
            result = fanout_recall("test query", core_memory_result=cm_result, pipehouse_cfg=None)

        assert result.evidence[0].bead_id == "b1"
        assert result.metadata.get("fanout_stores") == ["core_memory"]
        assert result.metadata.get("unavailable_stores") == []


class TestNormalizeScores:
    def test_empty_list(self) -> None:
        assert _normalize_scores([]) == []

    def test_single_item(self) -> None:
        item = _cm_item("b1", score=0.5)
        result = _normalize_scores([item])
        assert result[0].score == 0.5

    def test_all_none_scores(self) -> None:
        items = [_cm_item("b1", score=None), _cm_item("b2", score=None)]
        result = _normalize_scores(items)
        assert all(i.score is None for i in result)

    def test_normalizes_to_zero_one(self) -> None:
        items = [_cm_item("b1", score=0.2), _cm_item("b2", score=0.4), _cm_item("b3", score=0.6)]
        result = _normalize_scores(items)
        scores = [i.score for i in result]
        assert min(scores) == pytest.approx(0.0)
        assert max(scores) == pytest.approx(1.0)

    def test_equal_scores_unchanged(self) -> None:
        items = [_cm_item("b1", score=0.5), _cm_item("b2", score=0.5)]
        result = _normalize_scores(items)
        assert all(i.score == 0.5 for i in result)


class TestStoreWeights:
    def test_two_value_weight_config_maps_core_and_pipehouse(self) -> None:
        with patch.dict(os.environ, {"CORE_MEMORY_STORE_WEIGHTS": "2.0,0.25"}, clear=False):
            assert _parse_store_weights() == {"core_memory": 2.0, "pipehouse": 0.25}

    def test_legacy_three_value_weight_config_preserves_pipehouse_slot(self) -> None:
        with patch.dict(os.environ, {"CORE_MEMORY_STORE_WEIGHTS": "2.0,99.0,0.25"}, clear=False):
            assert _parse_store_weights() == {"core_memory": 2.0, "pipehouse": 0.25}


class TestResolveUnifyingIds:
    def test_no_unifying_ids(self) -> None:
        items = [_cm_item("b1"), _external_item("x1")]
        result = _resolve_unifying_ids(items)
        assert len(result) == 2

    def test_external_item_grouped_into_cm_primary(self) -> None:
        uid = "video-abc-123"
        cm = _cm_item("b1")
        cm.unifying_id = uid
        external = _external_item("x1", unifying_id=uid)
        result = _resolve_unifying_ids([cm, external])
        assert len(result) == 1
        assert result[0].source_store == "core_memory"
        assert result[0].bead_id == "b1"
        assert "x1" in result[0].metadata.get("unified_with", [])

    def test_unmatched_unifying_id_preserved(self) -> None:
        external = _external_item("x1", unifying_id="uid-no-cm-match")
        cm = _cm_item("b1")
        result = _resolve_unifying_ids([cm, external])
        assert len(result) == 2

    def test_multiple_external_items_same_uid(self) -> None:
        uid = "doc-xyz"
        cm = _cm_item("b1")
        cm.unifying_id = uid
        x1 = _external_item("x1", unifying_id=uid)
        x2 = _external_item("x2", unifying_id=uid)
        result = _resolve_unifying_ids([cm, x1, x2])
        assert len(result) == 1
        unified = result[0].metadata.get("unified_with", [])
        assert "x1" in unified
        assert "x2" in unified


class TestFanoutNoAdapters:
    def test_returns_core_memory_unchanged_when_no_adapters(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))
        result = fanout_recall("test query", core_memory_result=cm_result, pipehouse_cfg=None)
        assert result.evidence[0].bead_id == "b1"
        assert result.metadata.get("fanout_stores") == ["core_memory"]
        assert result.metadata.get("unavailable_stores") == []


class TestFanoutPipeHouse:
    def test_merged_evidence_from_core_memory_and_pipehouse(self) -> None:
        cm_result = _recall_result(_cm_item("b1", score=0.8))
        pipehouse_items = [_pipehouse_item("p1", score=0.7)]

        with patch(
            "core_memory.retrieval.adapters.pipehouse_adapter.retrieve",
            return_value=pipehouse_items,
        ) as mock_ph:
            result = fanout_recall(
                "why did COGS increase",
                core_memory_result=cm_result,
                pipehouse_cfg={"base_url": "http://ph.local"},
            )

        mock_ph.assert_called_once()
        assert result.metadata.get("fanout_stores") == ["core_memory", "pipehouse"]
        assert result.metadata.get("unavailable_stores") == []

        stores = {e.source_store for e in result.evidence}
        assert stores == {"core_memory", "pipehouse"}

    def test_evidence_sorted_by_score_descending(self) -> None:
        cm_result = _recall_result(_cm_item("b1", score=0.5))
        pipehouse_items = [_pipehouse_item("p1", score=1.0)]

        with patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=pipehouse_items):
            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                pipehouse_cfg={"base_url": "http://x"},
            )

        scores = [e.score for e in result.evidence if e.score is not None]
        assert scores == sorted(scores, reverse=True)

    def test_pipehouse_timeout_marks_unavailable(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))

        def _slow_pipehouse(*args: Any, **kwargs: Any) -> list[EvidenceItem]:
            time.sleep(10)
            return []

        with patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", side_effect=_slow_pipehouse), \
             patch("core_memory.retrieval.fanout._FANOUT_TIMEOUT", 0.05):
            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                pipehouse_cfg={"base_url": "http://x"},
            )

        assert "pipehouse" in result.metadata.get("unavailable_stores", [])
        assert {e.source_store for e in result.evidence} == {"core_memory"}

    def test_pipehouse_exception_marks_unavailable(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))

        with patch(
            "core_memory.retrieval.adapters.pipehouse_adapter.retrieve",
            side_effect=RuntimeError("network error"),
        ):
            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                pipehouse_cfg={"base_url": "http://x"},
            )

        assert "pipehouse" in result.metadata.get("unavailable_stores", [])
        assert {e.source_store for e in result.evidence} == {"core_memory"}

    def test_shared_unifying_id_deduplicates_pipehouse_into_cm(self) -> None:
        uid = "video-session-42"
        cm_item = _cm_item("b1")
        cm_item.unifying_id = uid
        cm_result = _recall_result(cm_item)
        pipehouse_items = [_pipehouse_item("p1", score=0.95, unifying_id=uid)]

        with patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=pipehouse_items):
            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                pipehouse_cfg={"base_url": "http://x"},
            )

        assert [e for e in result.evidence if e.source_store == "pipehouse"] == []
        cm_primary = next(e for e in result.evidence if e.source_store == "core_memory")
        assert "p1" in cm_primary.metadata.get("unified_with", [])


class TestEvidenceItemSchema:
    def test_default_source_store_is_core_memory(self) -> None:
        item = EvidenceItem(bead_id="b1")
        assert item.source_store == "core_memory"
        assert item.source_ref == ""
        assert item.unifying_id is None

    def test_roundtrip_preserves_external_fields(self) -> None:
        item = EvidenceItem(
            bead_id="b1",
            source_store="external_archive",
            source_ref="chunk-99",
            unifying_id="uid-xyz",
        )
        d = item.to_dict()
        restored = EvidenceItem.from_dict(d)
        assert restored.source_store == "external_archive"
        assert restored.source_ref == "chunk-99"
        assert restored.unifying_id == "uid-xyz"
