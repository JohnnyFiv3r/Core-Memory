from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.semantic_index import build_semantic_index, semantic_lookup


class TestSemanticBuildLockSlice59A(unittest.TestCase):
    def _seed_store(self, root: Path) -> None:
        s = MemoryStore(str(root))
        s.add_bead(type="decision", title="Latency", summary=["cut p95"], session_id="s1", source_turn_ids=["t1"])

    def test_build_returns_retryable_when_lock_is_held(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "hash",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            },
            clear=False,
        ):
            root = Path(td)
            self._seed_store(root)

            lock = root / ".beads" / "semantic" / "build.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"acquired_at": datetime.now(timezone.utc).isoformat(), "pid": 1234}),
                encoding="utf-8",
            )

            out = build_semantic_index(root)
            self.assertFalse(out.get("ok"))
            self.assertTrue(bool(out.get("retryable")))
            err = out.get("error") or {}
            self.assertEqual("semantic_build_lock_held", err.get("code"))

            q = root / ".beads" / "semantic" / "rebuild-queue.json"
            self.assertTrue(q.exists())

    def test_stale_lock_is_reclaimed_and_build_completes(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "hash",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            },
            clear=False,
        ):
            root = Path(td)
            self._seed_store(root)

            stale_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            lock = root / ".beads" / "semantic" / "build.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"acquired_at": stale_ts, "pid": 1}), encoding="utf-8")

            out = build_semantic_index(root)
            self.assertTrue(out.get("ok"))
            self.assertFalse(lock.exists())
            self.assertTrue(Path(out.get("manifest") or "").exists())

    def test_lookup_degrades_when_cold_start_build_is_lock_held(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "hash",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            },
            clear=False,
        ):
            root = Path(td)
            lock = root / ".beads" / "semantic" / "build.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"acquired_at": datetime.now(timezone.utc).isoformat(), "pid": 1234}),
                encoding="utf-8",
            )

            out = semantic_lookup(root, "latency", k=3)
            self.assertTrue(out.get("ok"))
            self.assertTrue(bool(out.get("degraded")))
            self.assertIn("semantic_build_lock_held", out.get("warnings") or [])

    def test_lookup_required_mode_returns_unavailable_when_lock_held(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "hash",
                "CORE_MEMORY_VECTOR_BACKEND": "local-faiss",
            },
            clear=False,
        ):
            root = Path(td)
            lock = root / ".beads" / "semantic" / "build.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"acquired_at": datetime.now(timezone.utc).isoformat(), "pid": 1234}),
                encoding="utf-8",
            )

            out = semantic_lookup(root, "latency", k=3, mode="required")
            self.assertFalse(out.get("ok"))
            self.assertEqual("semantic_backend_unavailable", ((out.get("error") or {}).get("code") or ""))
            self.assertIn("semantic_build_lock_held", out.get("warnings") or [])


if __name__ == "__main__":
    unittest.main()
