from __future__ import annotations

import unittest
from pathlib import Path


class TestSemanticBackendDocsSlice57A(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[1]

    def test_semantic_backend_modes_doc_exists_and_mentions_prod_recommendations(self):
        p = self.repo / "docs" / "semantic_backend_modes.md"
        self.assertTrue(p.exists())
        text = p.read_text(encoding="utf-8").lower()
        self.assertIn("faiss", text)
        self.assertIn("qdrant", text)
        self.assertIn("pgvector", text)
        self.assertIn("recommended", text)
        self.assertIn("multi-worker", text)

    def test_public_surfaces_reference_semantic_backend_guidance(self):
        readme = (self.repo / "README.md").read_text(encoding="utf-8").lower()
        pub = (self.repo / "docs" / "public_surface.md").read_text(encoding="utf-8").lower()
        canon = (self.repo / "docs" / "canonical_surfaces.md").read_text(encoding="utf-8").lower()

        for text in [readme, pub, canon]:
            self.assertIn("qdrant", text)
            self.assertIn("pgvector", text)
            self.assertIn("faiss", text)


if __name__ == "__main__":
    unittest.main()
