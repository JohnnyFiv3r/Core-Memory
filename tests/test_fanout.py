"""Tests for multi-store recall fan-out (#15).

Three fixture scenarios per PRD:
1. Fan-out with both adapters returning results → merged, normalized evidence list
2. Ragie times out → unavailable_stores=["ragie"], PipeHouse + Core Memory results present
3. Two items share core_memory_unifying_id → grouped, Ragie item deduplicated into primary
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from core_memory.retrieval.contracts import EvidenceItem, RecallResult
from core_memory.retrieval.fanout import fanout_recall, _normalize_scores, _resolve_unifying_ids


# ── helpers ──────────────────────────────────────────────────────────────────

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


def _ragie_item(chunk_id: str, score: float | None = 0.9, unifying_id: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        bead_id="",
        type="document_chunk",
        title=f"Ragie chunk {chunk_id}",
        content_excerpt="ragie chunk content",
        score=score,
        source_store="ragie",
        source_ref=chunk_id,
        unifying_id=unifying_id,
    )


def _pipehouse_item(record_id: str, score: float | None = 0.7) -> EvidenceItem:
    return EvidenceItem(
        bead_id="",
        type="data_insight",
        title=f"PipeHouse record {record_id}",
        content_excerpt="pipehouse insight",
        score=score,
        source_store="pipehouse",
        source_ref=record_id,
    )


def _recall_result(*items: EvidenceItem) -> RecallResult:
    return RecallResult(
        answer="CM answer",
        evidence=list(items),
        status="answered",
    )


# ── normalize_scores ──────────────────────────────────────────────────────────

class TestNormalizeScores:
    def test_empty_list(self) -> None:
        assert _normalize_scores([]) == []

    def test_single_item(self) -> None:
        item = _cm_item("b1", score=0.5)
        result = _normalize_scores([item])
        assert result[0].score == 0.5  # unchanged when only one item

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


# ── resolve_unifying_ids ──────────────────────────────────────────────────────

class TestResolveUnifyingIds:
    def test_no_unifying_ids(self) -> None:
        items = [_cm_item("b1"), _ragie_item("r1")]
        result = _resolve_unifying_ids(items)
        assert len(result) == 2

    def test_ragie_grouped_into_cm_primary(self) -> None:
        uid = "video-abc-123"
        cm = _cm_item("b1")
        cm.unifying_id = uid
        ragie = _ragie_item("r1", unifying_id=uid)
        result = _resolve_unifying_ids([cm, ragie])
        # Ragie item removed from top-level
        assert len(result) == 1
        assert result[0].source_store == "core_memory"
        assert result[0].bead_id == "b1"
        # Ragie source_ref attached to primary metadata
        assert "r1" in result[0].metadata.get("unified_with", [])

    def test_unmatched_unifying_id_preserved(self) -> None:
        ragie = _ragie_item("r1", unifying_id="uid-no-cm-match")
        cm = _cm_item("b1")
        result = _resolve_unifying_ids([cm, ragie])
        assert len(result) == 2

    def test_multiple_ragie_chunks_same_uid(self) -> None:
        uid = "doc-xyz"
        cm = _cm_item("b1")
        cm.unifying_id = uid
        r1 = _ragie_item("r1", unifying_id=uid)
        r2 = _ragie_item("r2", unifying_id=uid)
        result = _resolve_unifying_ids([cm, r1, r2])
        assert len(result) == 1
        unified = result[0].metadata.get("unified_with", [])
        assert "r1" in unified
        assert "r2" in unified


# ── fanout_recall — no external adapters ─────────────────────────────────────

class TestFanoutNoAdapters:
    def test_returns_core_memory_unchanged_when_no_adapters(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))
        result = fanout_recall("test query", core_memory_result=cm_result, ragie_cfg=None, pipehouse_cfg=None)
        assert result.evidence[0].bead_id == "b1"
        assert result.metadata.get("fanout_stores") == ["core_memory"]
        assert result.metadata.get("unavailable_stores") == []


# ── fanout_recall — both adapters return results ──────────────────────────────

class TestFanoutBothAdapters:
    def test_merged_evidence_from_all_three_stores(self) -> None:
        cm_result = _recall_result(_cm_item("b1", score=0.8))

        ragie_items = [_ragie_item("r1", score=0.9)]
        pipehouse_items = [_pipehouse_item("p1", score=0.7)]

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", return_value=ragie_items) as mock_ragie, \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=pipehouse_items) as mock_ph:

            result = fanout_recall(
                "why did COGS increase",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "test-key"},
                pipehouse_cfg={"base_url": "http://ph.local"},
            )

        assert result.metadata.get("fanout_stores") == ["core_memory", "ragie", "pipehouse"]
        assert result.metadata.get("unavailable_stores") == []

        stores = {e.source_store for e in result.evidence}
        assert "core_memory" in stores
        assert "ragie" in stores
        assert "pipehouse" in stores

    def test_evidence_sorted_by_score_descending(self) -> None:
        cm_result = _recall_result(_cm_item("b1", score=0.5))
        ragie_items = [_ragie_item("r1", score=1.0)]

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", return_value=ragie_items), \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=[]):

            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "k"},
                pipehouse_cfg={"base_url": "http://x"},
            )

        scores = [e.score for e in result.evidence if e.score is not None]
        assert scores == sorted(scores, reverse=True)


# ── fanout_recall — Ragie timeout ─────────────────────────────────────────────

class TestFanoutRagieTimeout:
    def test_ragie_timeout_marks_unavailable(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))
        pipehouse_items = [_pipehouse_item("p1", score=0.6)]

        def _slow_ragie(*args: Any, **kwargs: Any) -> list[EvidenceItem]:
            time.sleep(10)
            return []

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", side_effect=_slow_ragie), \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=pipehouse_items), \
             patch("core_memory.retrieval.fanout._FANOUT_TIMEOUT", 0.05):

            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "k"},
                pipehouse_cfg={"base_url": "http://x"},
            )

        assert "ragie" in result.metadata.get("unavailable_stores", [])
        stores = {e.source_store for e in result.evidence}
        assert "core_memory" in stores
        assert "pipehouse" in stores
        assert "ragie" not in stores

    def test_ragie_exception_marks_unavailable(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", side_effect=RuntimeError("network error")), \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=[]):

            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "k"},
                pipehouse_cfg={"base_url": "http://x"},
            )

        assert "ragie" in result.metadata.get("unavailable_stores", [])


# ── fanout_recall — unifying ID grouping ─────────────────────────────────────

class TestFanoutUnifyingId:
    def test_shared_unifying_id_deduplicates_ragie_into_cm(self) -> None:
        uid = "video-session-42"
        cm_item = _cm_item("b1")
        cm_item.unifying_id = uid
        cm_result = _recall_result(cm_item)

        ragie_items = [_ragie_item("r1", score=0.95, unifying_id=uid)]

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", return_value=ragie_items), \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=[]):

            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "k"},
                pipehouse_cfg={"base_url": "http://x"},
            )

        # Ragie item is NOT in top-level evidence
        ragie_top_level = [e for e in result.evidence if e.source_store == "ragie"]
        assert len(ragie_top_level) == 0

        # CM primary has unified_with metadata
        cm_primary = next(e for e in result.evidence if e.source_store == "core_memory")
        assert "r1" in cm_primary.metadata.get("unified_with", [])

    def test_no_unifying_id_keeps_both(self) -> None:
        cm_result = _recall_result(_cm_item("b1"))
        ragie_items = [_ragie_item("r1")]

        with patch("core_memory.retrieval.adapters.ragie_adapter.retrieve", return_value=ragie_items), \
             patch("core_memory.retrieval.adapters.pipehouse_adapter.retrieve", return_value=[]):

            result = fanout_recall(
                "test",
                core_memory_result=cm_result,
                ragie_cfg={"api_key": "k"},
                pipehouse_cfg={"base_url": "http://x"},
            )

        stores = {e.source_store for e in result.evidence}
        assert "core_memory" in stores
        assert "ragie" in stores


# ── ragie_adapter unit tests ──────────────────────────────────────────────────

class TestRagieAdapterNormalize:
    def test_scores_normalized(self) -> None:
        from core_memory.retrieval.adapters.ragie_adapter import _normalize_scores
        items = [
            EvidenceItem(bead_id="", score=2.0, source_store="ragie", source_ref="a"),
            EvidenceItem(bead_id="", score=4.0, source_store="ragie", source_ref="b"),
        ]
        result = _normalize_scores(items)
        scores = {i.source_ref: i.score for i in result}
        assert scores["a"] == pytest.approx(0.0)
        assert scores["b"] == pytest.approx(1.0)

    def test_unifying_id_extracted_from_document_metadata(self) -> None:
        from core_memory.retrieval.adapters import ragie_adapter
        chunk_payload = {
            "scored_chunks": [{
                "id": "chunk-001",
                "document_id": "doc-001",
                "document_name": "Contract Q4",
                "score": 0.9,
                "text": "COGS anomaly details here",
                "index": 3,
                "metadata": {},
                "document_metadata": {"core_memory_unifying_id": "vid-abc"},
                "links": {},
            }]
        }
        import io
        import json
        import urllib.request

        class _FakeResponse:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self.status = 200
            def read(self) -> bytes:
                return self._data
            def __enter__(self) -> "_FakeResponse":
                return self
            def __exit__(self, *args: object) -> None:
                pass

        with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(chunk_payload).encode())):
            items = ragie_adapter.retrieve("test query", api_key="k")

        assert len(items) == 1
        assert items[0].unifying_id == "vid-abc"
        assert items[0].source_store == "ragie"
        assert items[0].source_ref == "chunk-001"
        assert items[0].content_excerpt == "COGS anomaly details here"


# ── EvidenceItem schema ───────────────────────────────────────────────────────

class TestEvidenceItemSchema:
    def test_default_source_store_is_core_memory(self) -> None:
        item = EvidenceItem(bead_id="b1")
        assert item.source_store == "core_memory"
        assert item.source_ref == ""
        assert item.unifying_id is None

    def test_roundtrip_preserves_new_fields(self) -> None:
        item = EvidenceItem(
            bead_id="b1",
            source_store="ragie",
            source_ref="chunk-99",
            unifying_id="uid-xyz",
        )
        d = item.to_dict()
        restored = EvidenceItem.from_dict(d)
        assert restored.source_store == "ragie"
        assert restored.source_ref == "chunk-99"
        assert restored.unifying_id == "uid-xyz"
