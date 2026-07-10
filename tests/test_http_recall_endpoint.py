"""HTTP /v1/memory/recall parity with the MCP recall tool.

Both wire surfaces share core_memory.integrations.recall_payload, so they must
return the same RecallResult contract for the same store and query.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestHttpRecallEndpoint(unittest.TestCase):
    def setUp(self):
        self._old_semantic_mode = os.environ.get("CORE_MEMORY_CANONICAL_SEMANTIC_MODE")
        os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from core_memory.integrations.http.server import app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

    def tearDown(self):
        if getattr(self, "_old_semantic_mode", None) is None:
            os.environ.pop("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", None)
        else:
            os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = self._old_semantic_mode

    def _client(self):
        from fastapi.testclient import TestClient

        from core_memory.integrations.http.server import app

        return TestClient(app)

    def test_recall_returns_recall_result_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post("/v1/memory/recall", json={"root": root, "query": "anything", "effort": "low"})
            self.assertEqual(200, r.status_code)
            body = r.json()
            # RecallResult contract keys must be present; empty store must not 500.
            self.assertTrue(set(body) >= {"ok", "status", "evidence"})
            self.assertIn("tier_path", body)
            self.assertIn("steps", body)

    def test_recall_missing_query_is_400(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post("/v1/memory/recall", json={"root": root, "query": ""})
            self.assertEqual(400, r.status_code)
            body = r.json()
            self.assertEqual("cm.invalid_request", body["error"]["code"])
            self.assertEqual("query", body["error"]["data"]["field"])

    def test_recall_invalid_effort_is_400(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            for effort in ("bogus", "dynamic"):
                r = c.post("/v1/memory/recall", json={"root": root, "query": "q", "effort": effort})
                self.assertEqual(400, r.status_code, f"effort={effort}")
                body = r.json()
                self.assertEqual("cm.invalid_request", body["error"]["code"])
                self.assertEqual("effort", body["error"]["data"]["field"])

    def test_recall_parity_with_mcp_handler(self):
        from core_memory.integrations.mcp.tools.recall import recall_handler

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            query = "what changed in the deploy"
            c = self._client()
            http_body = c.post(
                "/v1/memory/recall",
                json={"root": root, "query": query, "effort": "medium", "include_raw": False},
            ).json()
            mcp_body = recall_handler({"root": root, "query": query, "effort": "medium", "include_raw": False})
            self.assertEqual(set(mcp_body.keys()), set(http_body.keys()))
            self.assertEqual(mcp_body.get("ok"), http_body.get("ok"))
            self.assertEqual(mcp_body.get("status"), http_body.get("status"))

    def test_recall_speaker_list_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            r = c.post(
                "/v1/memory/recall",
                json={"root": root, "query": "q", "effort": "low", "speaker": ["alice", "bob"]},
            )
            self.assertEqual(200, r.status_code)

    def test_product_effort_aliases_are_accepted_and_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            c = self._client()
            for requested, effective in (("instant", "low"), ("trace", "high")):
                r = c.post(
                    "/v1/memory/recall",
                    json={"root": root, "query": "q", "effort": requested},
                )
                self.assertEqual(200, r.status_code, f"effort={requested}")
                body = r.json()
                self.assertEqual(requested, body["metadata"]["requested_effort"])
                self.assertEqual(effective, body["metadata"]["effective_effort"])
                self.assertEqual(effective, body["planning"]["selected_effort"])

    def test_recall_hydrates_owned_section_to_exact_chunk_without_raw(self):
        from core_memory.persistence.store import MemoryStore
        from core_memory.runtime.ingest.chunk_turns import ingest_chunk_turns

        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            chunk_id = "chunk-airport-1"
            ingest_chunk_turns(
                root,
                [
                    {
                        "schema": "chunk_turn_record.v1",
                        "workspace_id": "workspace-1",
                        "source_document_id": "document-1",
                        "section_id": "section-1",
                        "chunk_id": chunk_id,
                        "chunk_index": 0,
                        "content_text": "ORD airport bid is 1.2 million dollars.",
                        "content_hash": "sha256:airport-bid-1",
                        "source_element_ids": ["element-1"],
                        "chunk_set_version": 1,
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
                                "chunk_set_version": 1,
                            },
                        },
                        "metadata": {"fixture": True},
                    }
                ],
            )
            section_id = MemoryStore(root).add_bead(
                type="document_reference",
                title="ORD airport bid pricing",
                summary=["Owned-ingestion section anchor for the ORD airport bid."],
                session_id="external",
                source_turn_ids=[chunk_id],
                retrieval_eligible=True,
                data_type_flag="document",
                source_kind="document",
                core_memory_unifying_id="raw-object-1",
                section_refs=[{"section_id": "section-1", "label": "Airport bids"}],
            )

            r = self._client().post(
                "/v1/memory/recall",
                json={
                    "root": root,
                    "query": "ORD airport bid pricing",
                    "effort": "trace",
                    "include_raw": False,
                    "hydration": {
                        "turn_sources": True,
                        "max_beads": 4,
                        "adjacent_before": 1,
                        "adjacent_after": 1,
                    },
                },
            )

            self.assertEqual(200, r.status_code)
            body = r.json()
            self.assertIsNone(body["raw"])
            self.assertIn(section_id, [row["bead_id"] for row in body["evidence"]])
            self.assertEqual("complete", body["hydration"]["status"])
            self.assertEqual(
                "cited_turns_plus_adjacent",
                body["hydration"]["request"]["turn_sources"],
            )
            hydrated = body["hydration"]["data"]["hydrated"]
            matching = [row for row in hydrated if row.get("turn", {}).get("turn_id") == chunk_id]
            self.assertEqual(1, len(matching))
            self.assertEqual(
                "ORD airport bid is 1.2 million dollars.",
                matching[0]["turn"]["turn_text"],
            )
