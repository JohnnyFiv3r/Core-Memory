import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.semantic_index import semantic_doctor


class TestSemanticDoctor(unittest.TestCase):
    def test_required_mode_reports_unavailable_and_next_step(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required"}, clear=False
        ):
            MemoryStore(td)
            out = semantic_doctor(Path(td))
            self.assertTrue(out.get("ok"))
            self.assertEqual("required", out.get("mode"))
            self.assertFalse(out.get("degraded_mode_enabled"))
            self.assertFalse(out.get("usable_backend"))
            self.assertIn("semantic-build", out.get("next_step") or "")

    def test_degraded_mode_reports_flag(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False
        ):
            MemoryStore(td)
            out = semantic_doctor(Path(td))
            self.assertEqual("degraded_allowed", out.get("mode"))
            self.assertTrue(out.get("degraded_mode_enabled"))

    def test_reports_usable_backend_when_manifest_rows_faiss_exist(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(td)
            sem = Path(td) / ".beads" / "semantic"
            sem.mkdir(parents=True, exist_ok=True)
            (sem / "manifest.json").write_text(json.dumps({"backend": "faiss-openai", "provider": "openai"}), encoding="utf-8")
            (sem / "rows.jsonl").write_text(json.dumps([{"bead_id": "b1"}]), encoding="utf-8")
            (sem / "index.faiss").write_text("x", encoding="utf-8")

            out = semantic_doctor(Path(td))
            self.assertTrue(out.get("usable_backend"))
            self.assertEqual("faiss-openai", out.get("backend"))


if __name__ == "__main__":
    unittest.main()
