from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.semantic_index import build_semantic_index, semantic_lookup


class TestSemanticRequiredModeProviderRegression(unittest.TestCase):
    def test_required_provider_build_does_not_succeed_with_lexical_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sem = root / ".beads" / "semantic"
            sem.mkdir(parents=True, exist_ok=True)

            with patch("core_memory.retrieval.semantic_index.build_visible_corpus", return_value=[]), \
                 patch("core_memory.retrieval.semantic_index._rows_from_corpus", return_value=[{"bead_id": "b1", "semantic_text": "hello world", "status": "open"}]), \
                 patch("core_memory.retrieval.semantic_index._embed_vectors", side_effect=RuntimeError("openai_embedding_failed:http_401")), \
                 patch.dict("os.environ", {
                     "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                     "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                     "CORE_MEMORY_EMBEDDINGS_MODEL": "text-embedding-3-small",
                 }, clear=False):
                out = build_semantic_index(root)

            self.assertFalse(out["ok"])
            self.assertEqual("semantic_build_invalid_state", out["error"]["code"])
            self.assertEqual("openai", out["error"]["detail"]["provider"])

    def test_required_lookup_surfaces_build_failure_detail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sem = root / ".beads" / "semantic"
            sem.mkdir(parents=True, exist_ok=True)
            (sem / "manifest.json").write_text(
                """
                {
                  "provider": "openai",
                  "model": "text-embedding-3-small",
                  "dimension": 0,
                  "backend": "lexical",
                  "vector_backend": "local-faiss",
                  "semantic_ready": false,
                  "last_build_error_code": "openai_embedding_failed:http_401"
                }
                """,
                encoding="utf-8",
            )
            (sem / "rows.jsonl").write_text('{"bead_id":"b1","semantic_text":"hello world","status":"open"}\n', encoding="utf-8")

            with patch.dict("os.environ", {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "CORE_MEMORY_EMBEDDINGS_MODEL": "text-embedding-3-small",
                "CORE_MEMORY_SEMANTIC_BUILD_ON_READ": "0",
            }, clear=False):
                out = semantic_lookup(root, "hello", k=5, mode="required")

            self.assertFalse(out["ok"])
            self.assertEqual("semantic_backend_unavailable", out["error"]["code"])
            self.assertEqual("build_failed_or_invalid", out["error"]["detail"]["reason"])
            self.assertEqual("openai_embedding_failed:http_401", out["error"]["detail"]["last_build_error_code"])


if __name__ == "__main__":
    unittest.main()
