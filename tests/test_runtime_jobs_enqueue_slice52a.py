from __future__ import annotations

import unittest
from unittest.mock import patch

from core_memory.runtime.jobs import enqueue_async_job


class TestRuntimeJobsEnqueueSlice52A(unittest.TestCase):
    @patch("core_memory.runtime.jobs.semantic_rebuild_queue_status")
    @patch("core_memory.runtime.jobs.enqueue_semantic_rebuild")
    def test_enqueue_semantic_rebuild(self, enqueue_semantic, semantic_status):
        enqueue_semantic.return_value = {"ok": True, "queued": True, "epoch": 4}
        semantic_status.return_value = {"ok": True, "kind": "semantic_rebuild", "queued": True, "pending": 1}

        out = enqueue_async_job("/tmp/x", kind="semantic-rebuild")

        self.assertTrue(out.get("ok"))
        self.assertEqual("core_memory.async_jobs.v1", out.get("schema_version"))
        self.assertEqual("semantic-rebuild", out.get("kind"))
        self.assertTrue((out.get("queue") or {}).get("queued"))
        self.assertTrue((out.get("status") or {}).get("queued"))

    @patch("core_memory.runtime.jobs.compaction_queue_status")
    @patch("core_memory.runtime.jobs.enqueue_compaction_event")
    def test_enqueue_compaction(self, enqueue_compaction, compaction_status):
        enqueue_compaction.return_value = {"ok": True, "queue_depth": 2, "id": "cq-1"}
        compaction_status.return_value = {"ok": True, "kind": "compaction", "queue_depth": 2}

        out = enqueue_async_job(
            "/tmp/x",
            kind="compaction",
            event={"runId": "r1"},
            ctx={"sessionId": "s1"},
        )

        self.assertTrue(out.get("ok"))
        self.assertEqual("core_memory.async_jobs.v1", out.get("schema_version"))
        self.assertEqual("compaction", out.get("kind"))
        self.assertEqual(2, (out.get("queue") or {}).get("queue_depth"))
        self.assertEqual(2, (out.get("status") or {}).get("queue_depth"))

    def test_enqueue_rejects_unknown_kind(self):
        out = enqueue_async_job("/tmp/x", kind="does-not-exist")
        self.assertFalse(out.get("ok"))
        self.assertEqual("core_memory.async_jobs.v1", out.get("schema_version"))
        err = out.get("error") or {}
        self.assertEqual("unknown_kind", err.get("code"))
        self.assertIn("allowed", err)

    @patch("core_memory.runtime.jobs.side_effect_queue_status")
    @patch("core_memory.runtime.jobs.enqueue_side_effect_event")
    def test_enqueue_dreamer_side_effect_kind(self, enqueue_side_effect, side_effect_status):
        enqueue_side_effect.return_value = {"ok": True, "id": "se-1", "queue_depth": 1, "kind": "dreamer-run"}
        side_effect_status.return_value = {"ok": True, "kind": "side_effects", "queue_depth": 1}

        out = enqueue_async_job("/tmp/x", kind="dreamer-run", event={"session_id": "s1"})
        self.assertTrue(out.get("ok"))
        self.assertEqual("dreamer-run", out.get("kind"))
        self.assertEqual(1, ((out.get("status") or {}).get("queue_depth") or 0))


if __name__ == "__main__":
    unittest.main()
