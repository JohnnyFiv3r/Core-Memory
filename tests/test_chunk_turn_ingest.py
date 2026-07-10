from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core_memory.persistence.source_hydration import hydrate_bead_sources_for_root
from core_memory.runtime.ingest.chunk_turns import ingest_chunk_turns, list_chunk_turns


def _record(
    chunk_id: str,
    *,
    index: int,
    version: int = 1,
    text: str = "Airport bid details",
) -> dict:
    return {
        "schema": "chunk_turn_record.v1",
        "workspace_id": "workspace-1",
        "source_document_id": "document-1",
        "section_id": "section-1",
        "chunk_id": chunk_id,
        "chunk_index": index,
        "content_text": text,
        "content_hash": f"sha256:{version}:{index}:{text}",
        "source_element_ids": [f"element-{index}"],
        "chunk_set_version": version,
        "hydration_ref": {
            "schema": "hydration_ref.v2",
            "version": 2,
            "kind": "chunk_turn",
            "source": {
                "workspace_id": "workspace-1",
                "source_document_id": "document-1",
            },
            "target": {
                "chunk_turn_id": chunk_id,
                "core_memory_unifying_id": "raw-object-1",
                "chunk_set_version": version,
            },
        },
        "metadata": {"fixture": True},
    }


class TestChunkTurnIngest(unittest.TestCase):
    def test_ingest_is_idempotent_and_native_hydration_preserves_adjacency(self):
        with tempfile.TemporaryDirectory() as td:
            records = [
                _record("chunk-2", index=1, text="DEN bid is 980 thousand"),
                _record("chunk-1", index=0, text="ORD bid is 1.2 million"),
            ]

            first = ingest_chunk_turns(td, records)
            second = ingest_chunk_turns(td, records)
            hydrated = hydrate_bead_sources_for_root(
                root=td,
                turn_ids=["chunk-2"],
                include_tools=False,
                before=1,
                after=0,
            )

            self.assertEqual(2, first["created_count"])
            self.assertEqual(0, first["existing_count"])
            self.assertEqual(0, second["created_count"])
            self.assertEqual(2, second["existing_count"])
            self.assertEqual(1, len(hydrated["hydrated"]))
            entry = hydrated["hydrated"][0]
            self.assertEqual("DEN bid is 980 thousand", entry["turn"]["turn_text"])
            self.assertEqual("chunk-1", entry["adjacent"]["before"][0]["turn_id"])
            self.assertEqual(1, entry["turn"]["metadata"]["chunk_set_version"])

            lines = list((Path(td) / ".turns").glob("session-*.jsonl"))[0].read_text().splitlines()
            self.assertEqual(2, len(lines))

    def test_version_filter_supports_gc_planning(self):
        with tempfile.TemporaryDirectory() as td:
            ingest_chunk_turns(
                td,
                [
                    _record("chunk-v1", index=0, version=1),
                    _record("chunk-v2", index=0, version=2),
                ],
            )

            all_rows = list_chunk_turns(td, core_memory_unifying_id="raw-object-1")
            v1_rows = list_chunk_turns(
                td,
                core_memory_unifying_id="raw-object-1",
                chunk_set_version_lte=1,
            )

            self.assertEqual(2, all_rows["count"])
            self.assertEqual(1, v1_rows["count"])
            self.assertEqual("chunk-v1", v1_rows["chunks"][0]["chunk_id"])

    def test_immutable_chunk_id_conflict_fails_without_second_write(self):
        with tempfile.TemporaryDirectory() as td:
            ingest_chunk_turns(td, [_record("chunk-1", index=0, text="original")])

            with self.assertRaisesRegex(ValueError, "immutable chunk_id conflict"):
                ingest_chunk_turns(td, [_record("chunk-1", index=0, text="changed")])

            lines = list((Path(td) / ".turns").glob("session-*.jsonl"))[0].read_text().splitlines()
            self.assertEqual(1, len(lines))

    def test_concurrent_retry_writes_one_archive_record(self):
        with tempfile.TemporaryDirectory() as td:
            record = _record("chunk-concurrent", index=0)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda _attempt: ingest_chunk_turns(td, [record]),
                        range(2),
                    )
                )

            self.assertEqual([0, 1], sorted(result["created_count"] for result in results))
            lines = list((Path(td) / ".turns").glob("session-*.jsonl"))[0].read_text().splitlines()
            self.assertEqual(1, len(lines))

    def test_http_contract_ingests_and_lists_chunks(self):
        try:
            from fastapi.testclient import TestClient

            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        with tempfile.TemporaryDirectory() as td:
            client = TestClient(app)
            response = client.post(
                "/v1/memory/chunk-turns",
                json={"root": td, "records": [_record("chunk-http", index=0)]},
            )
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, response.json()["created_count"])

            listed = client.get(
                "/v1/memory/chunk-turns",
                params={"root": td, "core_memory_unifying_id": "raw-object-1"},
            )
            self.assertEqual(200, listed.status_code)
            self.assertEqual(1, listed.json()["count"])

    def test_invalid_hydration_ref_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            invalid = _record("chunk-2", index=1)
            invalid["hydration_ref"] = {"schema": "legacy.v1"}

            with self.assertRaisesRegex(ValueError, "hydration_ref.v2"):
                ingest_chunk_turns(
                    td,
                    [_record("chunk-1", index=0), invalid],
                )

            self.assertFalse((Path(td) / ".turns").exists())

    def test_duplicate_section_position_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "duplicate chunk_index"):
                ingest_chunk_turns(
                    td,
                    [
                        _record("chunk-1", index=0),
                        _record("chunk-2", index=0),
                    ],
                )

            self.assertFalse((Path(td) / ".turns").exists())


if __name__ == "__main__":
    unittest.main()
