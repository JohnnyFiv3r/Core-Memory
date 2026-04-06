from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.runtime.jobs import run_async_jobs


class TestRuntimeJobsRunSlice52A(unittest.TestCase):
    @patch("core_memory.runtime.jobs.async_jobs_status")
    @patch("core_memory.runtime.jobs.drain_compaction_queue")
    @patch("core_memory.runtime.jobs.semantic_rebuild_queue_status")
    def test_run_skips_semantic_when_not_queued(self, sem_status, drain_compaction, jobs_status):
        sem_status.return_value = {"ok": True, "queued": False, "pending": 0}
        drain_compaction.return_value = {"ok": True, "processed": 0, "failed": 0, "queue_depth": 0}
        jobs_status.return_value = {"ok": True, "pending_total": 0, "processable_now": 0, "queues": {}}

        out = run_async_jobs("/tmp/x", run_semantic=True, max_compaction=2)

        self.assertTrue(out.get("ok"))
        self.assertEqual("core_memory.async_jobs.v1", out.get("schema_version"))
        self.assertFalse((out.get("semantic_run") or {}).get("ran"))
        self.assertEqual("not_queued", (out.get("semantic_run") or {}).get("reason"))
        drain_compaction.assert_called_once()

    @patch("core_memory.runtime.jobs.async_jobs_status")
    @patch("core_memory.runtime.jobs.drain_compaction_queue")
    @patch("core_memory.runtime.jobs.build_semantic_index")
    @patch("core_memory.runtime.jobs.semantic_rebuild_queue_status")
    def test_run_executes_semantic_when_queued(self, sem_status, build_semantic, drain_compaction, jobs_status):
        sem_status.return_value = {"ok": True, "queued": True, "pending": 1}
        build_semantic.return_value = {"ok": True, "backend": "lexical"}
        drain_compaction.return_value = {"ok": True, "processed": 1, "failed": 0, "queue_depth": 0}
        jobs_status.return_value = {"ok": True, "pending_total": 0, "processable_now": 0, "queues": {}}

        out = run_async_jobs("/tmp/x", run_semantic=True, max_compaction=1)

        self.assertTrue(out.get("ok"))
        self.assertTrue((out.get("semantic_run") or {}).get("ran"))
        self.assertTrue((out.get("semantic_run") or {}).get("ok"))
        build_semantic.assert_called_once()

    @patch("core_memory.runtime.jobs.async_jobs_status")
    @patch("core_memory.runtime.jobs.drain_compaction_queue")
    @patch("core_memory.runtime.jobs.build_semantic_index")
    @patch("core_memory.runtime.jobs.semantic_rebuild_queue_status")
    def test_run_returns_not_ok_when_substep_fails(self, sem_status, build_semantic, drain_compaction, jobs_status):
        sem_status.return_value = {"ok": True, "queued": True, "pending": 1}
        build_semantic.return_value = {"ok": False, "error": "build_failed"}
        drain_compaction.return_value = {"ok": True, "processed": 0, "failed": 0, "queue_depth": 0}
        jobs_status.return_value = {"ok": True, "pending_total": 0, "processable_now": 0, "queues": {}}

        out = run_async_jobs("/tmp/x", run_semantic=True, max_compaction=0)

        self.assertFalse(out.get("ok"))
        self.assertFalse((out.get("semantic_run") or {}).get("ok"))
        errs = out.get("errors") or []
        self.assertTrue(any((e or {}).get("code") == "semantic_run_failed" for e in errs))


if __name__ == "__main__":
    unittest.main()
