from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.jobs import async_jobs_status, compaction_queue_status, semantic_rebuild_queue_status


class TestRuntimeJobsSlice52A(unittest.TestCase):
    def test_status_defaults_when_no_queue_files_exist(self):
        with tempfile.TemporaryDirectory(prefix="cm-jobs-") as td:
            root = Path(td)
            sem = semantic_rebuild_queue_status(root)
            comp = compaction_queue_status(root, now_ts=100)

            self.assertTrue(sem.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", sem.get("schema_version"))
            self.assertFalse(sem.get("queued"))
            self.assertEqual(0, sem.get("pending"))

            self.assertTrue(comp.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", comp.get("schema_version"))
            self.assertEqual(0, comp.get("queue_depth"))
            self.assertEqual(0, comp.get("retry_ready"))
            self.assertEqual(0, comp.get("processable_now"))
            self.assertFalse(comp.get("circuit_open"))

    def test_semantic_queue_reports_coalesced_pending_flag(self):
        with tempfile.TemporaryDirectory(prefix="cm-jobs-") as td:
            root = Path(td)
            p = root / ".beads" / "semantic" / "rebuild-queue.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"queued": True, "queued_at": "2026-01-01T00:00:00Z", "epoch": 7}), encoding="utf-8")

            sem = semantic_rebuild_queue_status(root)
            self.assertTrue(sem.get("queued"))
            self.assertEqual(1, sem.get("pending"))
            self.assertEqual(7, sem.get("epoch"))

    def test_compaction_queue_status_respects_retry_and_circuit(self):
        with tempfile.TemporaryDirectory(prefix="cm-jobs-") as td:
            root = Path(td)
            q = root / ".beads" / "events" / "compaction-queue.json"
            s = root / ".beads" / "events" / "compaction-queue-state.json"
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(
                json.dumps(
                    [
                        {"id": "a", "next_retry_at": 90},
                        {"id": "b", "next_retry_at": 120},
                    ]
                ),
                encoding="utf-8",
            )
            s.write_text(
                json.dumps({"consecutive_failures": 3, "opened_until": 130, "last_error": "boom"}),
                encoding="utf-8",
            )

            comp = compaction_queue_status(root, now_ts=100)
            self.assertEqual(2, comp.get("queue_depth"))
            self.assertEqual(1, comp.get("retry_ready"))
            self.assertEqual(0, comp.get("processable_now"))  # circuit open blocks processing
            self.assertTrue(comp.get("circuit_open"))
            self.assertEqual(120, comp.get("next_retry_at"))

    def test_async_jobs_status_aggregates_queues(self):
        with tempfile.TemporaryDirectory(prefix="cm-jobs-") as td:
            root = Path(td)
            (root / ".beads" / "semantic").mkdir(parents=True, exist_ok=True)
            (root / ".beads" / "events").mkdir(parents=True, exist_ok=True)

            (root / ".beads" / "semantic" / "rebuild-queue.json").write_text(
                json.dumps({"queued": True, "queued_at": "x", "epoch": 2}), encoding="utf-8"
            )
            (root / ".beads" / "events" / "compaction-queue.json").write_text(
                json.dumps([{"id": "x", "next_retry_at": 0}]), encoding="utf-8"
            )

            out = async_jobs_status(root, now_ts=100)
            self.assertTrue(out.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", out.get("schema_version"))
            self.assertEqual(2, out.get("pending_total"))
            self.assertEqual(2, out.get("processable_now"))
            self.assertIn("semantic_rebuild", out.get("queues") or {})
            self.assertIn("compaction", out.get("queues") or {})


if __name__ == "__main__":
    unittest.main()
