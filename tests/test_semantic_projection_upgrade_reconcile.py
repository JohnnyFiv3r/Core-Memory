from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.retrieval.lifecycle import (
    enqueue_semantic_projection_upgrade_reconcile,
    enqueue_semantic_rebuild,
)
from core_memory.runtime.queue.jobs import run_async_jobs
from core_memory.schema.bead_projection import RETRIEVAL_TEXT_PROJECTION_VERSION


class TestSemanticProjectionUpgradeReconcile(unittest.TestCase):
    def _seed_persisted_semantic_index(self, root: str, *, projection_version: str | None = None) -> Path:
        sem = Path(root) / ".beads" / "semantic"
        sem.mkdir(parents=True, exist_ok=True)
        manifest = {
            "provider": "fastembed",
            "model": "qdrant-native",
            "vector_backend": "qdrant",
            "row_count": 1,
            "dirty": False,
        }
        if projection_version is not None:
            manifest["projection_version"] = projection_version
        (sem / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (sem / "rows.jsonl").write_text(
            json.dumps({"bead_id": "b1", "semantic_text": "old projection text", "status": "open"}) + "\n",
            encoding="utf-8",
        )
        return sem

    def test_projection_upgrade_escalates_existing_delta_queue_to_reconcile(self):
        with tempfile.TemporaryDirectory(prefix="cm-sem-proj-upgrade-") as td:
            sem = self._seed_persisted_semantic_index(td)
            delta = enqueue_semantic_rebuild(td, mode="delta")
            self.assertEqual("delta", delta.get("mode"))

            out = enqueue_semantic_projection_upgrade_reconcile(td)

            self.assertTrue(out.get("ok"))
            self.assertEqual("projection_upgrade_reconcile_required", out.get("reason"))
            q = json.loads((sem / "rebuild-queue.json").read_text(encoding="utf-8"))
            self.assertTrue(q.get("queued"))
            self.assertEqual("reconcile", q.get("mode"))

    def test_current_projection_does_not_enqueue_reconcile(self):
        with tempfile.TemporaryDirectory(prefix="cm-sem-proj-current-") as td:
            sem = self._seed_persisted_semantic_index(td, projection_version=RETRIEVAL_TEXT_PROJECTION_VERSION)

            out = enqueue_semantic_projection_upgrade_reconcile(td)

            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("queued"))
            self.assertEqual("projection_current", out.get("reason"))
            self.assertFalse((sem / "rebuild-queue.json").exists())

    def test_async_jobs_runs_reconcile_for_old_projection_not_delta(self):
        with tempfile.TemporaryDirectory(prefix="cm-sem-proj-jobs-") as td:
            self._seed_persisted_semantic_index(td)
            with (
                patch("core_memory.runtime.queue.jobs.build_semantic_index", return_value={"ok": True}) as build,
                patch("core_memory.runtime.queue.jobs.apply_semantic_delta", return_value={"ok": True}) as delta,
            ):
                out = run_async_jobs(td, run_semantic=True, max_compaction=0, max_side_effects=0)

            self.assertTrue(out.get("ok"))
            self.assertEqual(
                "projection_upgrade_reconcile_required",
                (out.get("semantic_projection_upgrade") or {}).get("reason"),
            )
            self.assertEqual("queued:reconcile", (out.get("semantic_run") or {}).get("reason"))
            build.assert_called_once()
            delta.assert_not_called()


if __name__ == "__main__":
    unittest.main()
