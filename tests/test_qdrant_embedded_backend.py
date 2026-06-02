from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import qdrant_client  # noqa: F401
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False


@unittest.skipUnless(_QDRANT_AVAILABLE, "qdrant-client not installed")
class TestQdrantEmbeddedBackend(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="cm-qdrant-")
        self.db_path = str(Path(self._td.name) / "qdrant")
        from core_memory.retrieval.vector_backend import QdrantBackend
        self.backend = QdrantBackend(
            collection_name="test_beads",
            path=self.db_path,
            dimensions=4,  # tiny dimension for test speed
        )

    def tearDown(self):
        self._td.cleanup()

    def _point(self, bead_id: str, eligible: bool = True, status: str = "open") -> dict:
        return {
            "bead_id": bead_id,
            "embedding": [0.1, 0.2, 0.3, 0.4],
            "metadata": {
                "retrieval_eligible": eligible,
                "status": status,
                "type": "lesson",
                "title": f"Bead {bead_id}",
            },
        }

    def test_collection_created(self):
        self.assertEqual(self.backend.count(), 0)

    def test_upsert_and_count(self):
        p = self._point("b1")
        self.backend.upsert(bead_id=p["bead_id"], embedding=p["embedding"], metadata=p["metadata"])
        self.assertEqual(self.backend.count(), 1)

    def test_upsert_idempotent(self):
        p = self._point("b2")
        self.backend.upsert(bead_id=p["bead_id"], embedding=p["embedding"], metadata=p["metadata"])
        self.backend.upsert(bead_id=p["bead_id"], embedding=p["embedding"], metadata=p["metadata"])
        self.assertEqual(self.backend.count(), 1)

    def test_filtered_search_excludes_ineligible(self):
        eligible = self._point("e1", eligible=True)
        ineligible = self._point("e2", eligible=False)
        self.backend.upsert(**{k: v for k, v in eligible.items() if k != "bead_id"}, bead_id=eligible["bead_id"])
        self.backend.upsert(**{k: v for k, v in ineligible.items() if k != "bead_id"}, bead_id=ineligible["bead_id"])
        results = self.backend.search(
            query_embedding=[0.1, 0.2, 0.3, 0.4],
            k=10,
            filters={"retrieval_eligible": True},
        )
        ids = [r["bead_id"] for r in results]
        self.assertIn("e1", ids)
        self.assertNotIn("e2", ids)

    def test_filtered_search_excludes_retracted(self):
        open_bead = self._point("a1", status="open")
        retracted = self._point("r1", status="retracted")
        self.backend.upsert(**{k: v for k, v in open_bead.items() if k != "bead_id"}, bead_id=open_bead["bead_id"])
        self.backend.upsert(**{k: v for k, v in retracted.items() if k != "bead_id"}, bead_id=retracted["bead_id"])
        results = self.backend.search(
            query_embedding=[0.1, 0.2, 0.3, 0.4],
            k=10,
            filters={"status": "open"},
        )
        ids = [r["bead_id"] for r in results]
        self.assertIn("a1", ids)
        self.assertNotIn("r1", ids)

    def test_retrieve_by_ids(self):
        p = self._point("b3")
        self.backend.upsert(bead_id=p["bead_id"], embedding=p["embedding"], metadata=p["metadata"])
        retrieved = self.backend.retrieve_by_ids(["b3"])
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0]["bead_id"], "b3")

    def test_delete(self):
        p = self._point("b4")
        self.backend.upsert(bead_id=p["bead_id"], embedding=p["embedding"], metadata=p["metadata"])
        self.assertEqual(self.backend.count(), 1)
        self.backend.delete("b4")
        self.assertEqual(self.backend.count(), 0)

    def test_embedded_mode_creates_dir(self):
        self.assertTrue(os.path.exists(self.db_path))


@unittest.skipUnless(_QDRANT_AVAILABLE, "qdrant-client not installed")
class TestQdrantNormalizeDefault(unittest.TestCase):
    def test_default_backend_is_qdrant(self):
        """CORE_MEMORY_VECTOR_BACKEND unset → resolves to qdrant."""
        old = os.environ.get("CORE_MEMORY_VECTOR_BACKEND")
        try:
            os.environ.pop("CORE_MEMORY_VECTOR_BACKEND", None)
            from core_memory.retrieval.semantic_index import _normalize_vector_backend, VECTOR_BACKEND_QDRANT
            result = _normalize_vector_backend(None)
            self.assertEqual(result, VECTOR_BACKEND_QDRANT)
        finally:
            if old is not None:
                os.environ["CORE_MEMORY_VECTOR_BACKEND"] = old

    def test_local_faiss_still_selectable(self):
        from core_memory.retrieval.semantic_index import _normalize_vector_backend, VECTOR_BACKEND_LOCAL_FAISS
        result = _normalize_vector_backend("local-faiss")
        self.assertEqual(result, VECTOR_BACKEND_LOCAL_FAISS)


if __name__ == "__main__":
    unittest.main()
