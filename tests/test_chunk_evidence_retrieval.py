from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.chunk_evidence import (
    CHUNK_EVIDENCE_ANCHOR_REASON,
    CHUNK_EVIDENCE_UNIT,
    resolve_semantic_hits,
)
from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.semantic_index import apply_semantic_delta, build_semantic_index, semantic_lookup
from core_memory.runtime.ingest.chunk_turns import ingest_chunk_turns


def _chunk(chunk_id: str, *, index: int, text: str, unifying_id: str = "raw-object-1") -> dict:
    return {
        "schema": "chunk_turn_record.v1",
        "workspace_id": "workspace-1",
        "source_document_id": "document-1",
        "section_id": "section-1",
        "chunk_id": chunk_id,
        "chunk_index": index,
        "content_text": text,
        "content_hash": f"sha256:{chunk_id}:{text}",
        "source_element_ids": [f"element-{index}"],
        "chunk_set_version": 1,
        "hydration_ref": {
            "schema": "hydration_ref.v2",
            "version": 2,
            "kind": "chunk_turn",
            "source": {"workspace_id": "workspace-1", "source_document_id": "document-1"},
            "target": {
                "chunk_turn_id": chunk_id,
                "core_memory_unifying_id": unifying_id,
                "chunk_set_version": 1,
            },
        },
        "metadata": {},
    }


def _section_bead(
    store: MemoryStore,
    chunk_ids: list[str],
    *,
    unifying_id: str = "raw-object-1",
    title: str = "Airport bid section",
) -> str:
    return store.add_bead(
        type="document_reference",
        title=title,
        summary=["Owned-ingestion section anchor"],
        session_id="external",
        source_turn_ids=chunk_ids,
        retrieval_eligible=True,
        data_type_flag="document.media",
        source_kind="document",
        core_memory_unifying_id=unifying_id,
        section_refs=[{"section_id": "section-1", "label": "Airport bids"}],
        hydration_ref={"schema": "hydration_ref.v2", "version": 2, "target": {"section_id": "section-1"}},
    )


class _EvidenceFirstBackend:
    def __init__(self):
        self.rows: list[tuple[str, dict]] = []

    def upsert_texts(self, bead_ids: list[str], texts: list[str], metadatas: list[dict]):
        rows_by_id = {bead_id: metadata for bead_id, metadata in self.rows}
        rows_by_id.update(
            {bead_id: dict(metadata or {}) for bead_id, metadata in zip(bead_ids, metadatas)}
        )
        self.rows = list(rows_by_id.items())

    def update_metadata(self, bead_id: str, metadata: dict):
        self.rows = [
            (row_id, dict(metadata or {}) if row_id == bead_id else row_metadata)
            for row_id, row_metadata in self.rows
        ]

    def delete(self, bead_id: str):
        self.rows = [(row_id, metadata) for row_id, metadata in self.rows if row_id != bead_id]

    def hybrid_search(self, query: str, k: int = 8, filters: dict | None = None):
        ranked = sorted(self.rows, key=lambda row: row[1].get("unit") == CHUNK_EVIDENCE_UNIT, reverse=True)
        return [
            {
                "bead_id": bead_id,
                "score": 0.99 if metadata.get("unit") == CHUNK_EVIDENCE_UNIT else 0.25,
                "metadata": metadata,
            }
            for bead_id, metadata in ranked[:k]
        ]

    def count(self) -> int:
        return len(self.rows)


class TestChunkEvidenceRetrieval(unittest.TestCase):
    def test_build_indexes_only_cited_chunks_and_lookup_resolves_to_parent_bead(self):
        backend = _EvidenceFirstBackend()
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_VECTOR_BACKEND": "qdrant",
                "CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS": "0",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            ingest_chunk_turns(
                td,
                [
                    _chunk("chunk-cited-1", index=0, text="ORD bid is 1.2 million"),
                    _chunk("chunk-cited-2", index=1, text="DEN bid is 980 thousand"),
                    _chunk("chunk-orphan", index=2, text="Uncited appendix"),
                ],
            )
            parent_id = _section_bead(MemoryStore(td), ["chunk-cited-1", "chunk-cited-2"])

            with patch("core_memory.retrieval.semantic_index.create_vector_backend", return_value=backend):
                built = build_semantic_index(Path(td))
                looked = semantic_lookup(Path(td), "DEN bid", k=5)

            self.assertTrue(built["ok"])
            self.assertEqual(3, built["entries"])
            manifest = json.loads((Path(td) / ".beads" / "semantic" / "manifest.json").read_text())
            self.assertEqual(2, manifest["evidence_row_count"])
            self.assertEqual([parent_id], [row["bead_id"] for row in looked["results"]])
            self.assertEqual(CHUNK_EVIDENCE_ANCHOR_REASON, looked["results"][0]["anchor_reason"])
            self.assertEqual(
                ["chunk-cited-1", "chunk-cited-2"],
                sorted(looked["results"][0]["evidence_turn_ids"]),
            )
            self.assertNotIn("chunk-orphan", looked["results"][0]["evidence_turn_ids"])

    def test_cross_document_chunk_citation_fails_closed(self):
        backend = _EvidenceFirstBackend()
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_VECTOR_BACKEND": "qdrant",
                "CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS": "0",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            ingest_chunk_turns(td, [_chunk("chunk-crossed", index=0, text="Wrong document")])
            _section_bead(MemoryStore(td), ["chunk-crossed"], unifying_id="different-object")

            with patch("core_memory.retrieval.semantic_index.create_vector_backend", return_value=backend):
                built = build_semantic_index(Path(td))

            self.assertTrue(built["ok"])
            self.assertEqual(1, built["entries"])
            self.assertTrue(all(metadata.get("unit") != CHUNK_EVIDENCE_UNIT for _, metadata in backend.rows))

    def test_chunk_ingest_queues_semantic_rebuild_only_for_new_records(self):
        with tempfile.TemporaryDirectory() as td:
            record = _chunk("chunk-queued", index=0, text="Queue semantic refresh")
            ingest_chunk_turns(td, [record])
            queue_path = Path(td) / ".beads" / "semantic" / "rebuild-queue.json"
            first = json.loads(queue_path.read_text())
            epoch = int(first["epoch"])

            ingest_chunk_turns(td, [record])
            second = json.loads(queue_path.read_text())

            self.assertTrue(first["queued"])
            self.assertEqual("delta", first["mode"])
            self.assertEqual(epoch, int(second["epoch"]))

    def test_fastembed_delta_uses_native_text_upsert_for_new_chunk_evidence(self):
        backend = _EvidenceFirstBackend()
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_VECTOR_BACKEND": "qdrant",
                "CORE_MEMORY_QDRANT_EXTERNAL_EMBEDDINGS": "0",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            ingest_chunk_turns(td, [_chunk("chunk-v1", index=0, text="Initial airport bid")])
            store = MemoryStore(td)
            _section_bead(store, ["chunk-v1"], title="Airport bid section v1")
            with patch("core_memory.retrieval.semantic_index.create_vector_backend", return_value=backend):
                first = build_semantic_index(Path(td))

            ingest_chunk_turns(td, [_chunk("chunk-v2", index=1, text="Updated airport bid")])
            _section_bead(store, ["chunk-v2"], title="Airport bid section v2")
            with patch("core_memory.retrieval.semantic_index.create_vector_backend", return_value=backend), patch(
                "core_memory.retrieval.semantic_index._embed_vectors",
                side_effect=AssertionError("FastEmbed delta must not call an external embedding provider"),
            ):
                delta = apply_semantic_delta(Path(td))

            self.assertTrue(first["ok"])
            self.assertTrue(delta["ok"])
            self.assertEqual(2, delta["embedded"])
            manifest = json.loads((Path(td) / ".beads" / "semantic" / "manifest.json").read_text())
            self.assertEqual("fastembed", manifest["provider"])
            self.assertEqual(2, manifest["evidence_row_count"])

    def test_resolver_deduplicates_multiple_chunk_hits(self):
        hits = [
            {
                "bead_id": "vector-1",
                "score": 0.91,
                "metadata": {
                    "unit": CHUNK_EVIDENCE_UNIT,
                    "parent_bead_id": "bead-parent",
                    "evidence_turn_id": "chunk-1",
                },
            },
            {
                "bead_id": "vector-2",
                "score": 0.87,
                "metadata": {
                    "unit": CHUNK_EVIDENCE_UNIT,
                    "parent_bead_id": "bead-parent",
                    "evidence_turn_id": "chunk-2",
                },
            },
        ]

        resolved = resolve_semantic_hits(hits)

        self.assertEqual(1, len(resolved))
        self.assertEqual("bead-parent", resolved[0]["bead_id"])
        self.assertEqual(["chunk-1", "chunk-2"], resolved[0]["evidence_turn_ids"])
        self.assertEqual([], resolve_semantic_hits(hits, row_by_id={}))

    def test_qdrant_hybrid_path_resolves_before_rerank(self):
        raw = [
            {
                "bead_id": "chunk-vector",
                "score": 0.92,
                "metadata": {
                    "unit": CHUNK_EVIDENCE_UNIT,
                    "parent_bead_id": "bead-parent",
                    "evidence_turn_id": "chunk-1",
                    "retrieval_eligible": True,
                },
            }
        ]
        with tempfile.TemporaryDirectory() as td, patch(
            "core_memory.retrieval.hybrid._configured_vector_backend", return_value="qdrant"
        ), patch("core_memory.retrieval.hybrid._qdrant_hybrid_rows", return_value=raw), patch(
            "core_memory.retrieval.hybrid._semantic_row_map",
            return_value={"chunk-vector": dict(raw[0]["metadata"])},
        ):
            result = hybrid_lookup(Path(td), "airport bid", k=3)

        self.assertTrue(result["ok"])
        self.assertEqual("bead-parent", result["results"][0]["bead_id"])
        self.assertEqual(CHUNK_EVIDENCE_ANCHOR_REASON, result["results"][0]["anchor_reason"])


if __name__ == "__main__":
    unittest.main()
