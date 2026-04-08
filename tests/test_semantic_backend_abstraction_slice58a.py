from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.semantic_index import build_semantic_index, semantic_doctor, semantic_lookup


class _FakeVectorBackend:
    def __init__(self):
        self.upserts: list[tuple[str, list[float], dict]] = []

    def upsert(self, bead_id: str, embedding: list[float], metadata: dict):
        self.upserts.append((bead_id, embedding, dict(metadata or {})))

    def search(self, query_embedding: list[float], k: int = 8, filters: dict | None = None):
        out = []
        for bead_id, emb, meta in self.upserts[: max(1, int(k))]:
            out.append({"bead_id": bead_id, "score": 0.91, "metadata": dict(meta or {})})
        return out

    def delete(self, bead_id: str):  # pragma: no cover - protocol completeness
        return None

    def count(self) -> int:
        return len(self.upserts)


class TestSemanticBackendAbstractionSlice58A(unittest.TestCase):
    def test_external_backend_build_and_lookup_path_is_interface_driven(self):
        fake = _FakeVectorBackend()

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_VECTOR_BACKEND": "qdrant",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "hash",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Scale queue", summary=["Use sharded workers"], session_id="main", source_turn_ids=["t1"])

            with patch("core_memory.retrieval.semantic_index.create_vector_backend", return_value=fake), patch(
                "core_memory.retrieval.semantic_index._embed_vectors",
                side_effect=lambda **kwargs: [[0.1, 0.2, 0.3] for _ in (kwargs.get("texts") or [])],
            ):
                built = build_semantic_index(Path(td))
                self.assertTrue(built.get("ok"))
                self.assertEqual("qdrant", built.get("backend"))
                self.assertGreaterEqual(len(fake.upserts), 1)

                looked = semantic_lookup(Path(td), "scale queue", k=3)
                self.assertTrue(looked.get("ok"))
                self.assertFalse(bool(looked.get("degraded")))
                self.assertEqual("qdrant", looked.get("backend"))
                self.assertGreaterEqual(len(looked.get("results") or []), 1)

            manifest = json.loads((Path(td) / ".beads" / "semantic" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("qdrant", manifest.get("backend"))
            self.assertEqual("qdrant", manifest.get("vector_backend"))

            with patch("core_memory.retrieval.semantic_index._external_backend_connectivity", return_value=(True, "")):
                doctor = semantic_doctor(Path(td))
            self.assertEqual("qdrant", doctor.get("backend"))
            self.assertEqual("distributed_safe", doctor.get("deployment_profile"))
            self.assertTrue(bool(doctor.get("usable_backend")))


if __name__ == "__main__":
    unittest.main()
