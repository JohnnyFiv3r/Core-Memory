from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.semantic_index import load_cached_bead_embeddings
from core_memory.retrieval.vector_backend import _coerce_embedding


class _FakeVectorBackend:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def get_embeddings(self, bead_ids: list[str]) -> dict[str, list[float]]:
        self.requested = list(bead_ids)
        return {bead_id: [float(position), 1.0] for position, bead_id in enumerate(bead_ids)}


class TestCachedSemanticEmbeddings(unittest.TestCase):
    def test_external_loader_reads_bead_vectors_without_embedding_provider(self):
        with tempfile.TemporaryDirectory(prefix="cm-cached-vectors-") as td:
            root = Path(td)
            semantic_dir = root / ".beads" / "semantic"
            semantic_dir.mkdir(parents=True, exist_ok=True)
            (semantic_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "semantic_ready": True,
                        "backend": "qdrant",
                        "vector_backend": "qdrant",
                        "provider": "openai",
                        "dimension": 2,
                    }
                ),
                encoding="utf-8",
            )
            (semantic_dir / "rows.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"bead_id": "b1", "unit": "bead"}),
                        json.dumps({"bead_id": "b1", "unit": "chunk_evidence"}),
                        json.dumps({"bead_id": "b2", "unit": "bead"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            backend = _FakeVectorBackend()

            with (
                patch(
                    "core_memory.retrieval.semantic_index._create_external_backend",
                    return_value=backend,
                ),
                patch(
                    "core_memory.retrieval.semantic_index._embed_vectors",
                    side_effect=AssertionError("cached reads must not call an embedding provider"),
                ),
            ):
                result = load_cached_bead_embeddings(root, ["b2", "missing"])

            self.assertEqual(["b2"], backend.requested)
            self.assertEqual({"b2": [0.0, 1.0]}, result)

    def test_backend_vector_shapes_are_normalized(self):
        self.assertEqual([1.0, 2.0], _coerce_embedding("[1, 2]"))
        self.assertEqual([3.0, 4.0], _coerce_embedding({"dense": [3, 4]}))
        self.assertEqual([], _coerce_embedding("not-a-vector"))


if __name__ == "__main__":
    unittest.main()
