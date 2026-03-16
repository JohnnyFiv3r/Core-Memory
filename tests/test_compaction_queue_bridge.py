import tempfile
import unittest
from unittest.mock import patch

from core_memory.integrations.openclaw_compaction_queue import enqueue_compaction_event, drain_compaction_queue


class TestCompactionQueueBridge(unittest.TestCase):
    def test_enqueue_and_drain_success(self):
        with tempfile.TemporaryDirectory() as td:
            enq = enqueue_compaction_event(event={"runId": "r1"}, ctx={"sessionKey": "main"}, root=td)
            self.assertTrue(enq.get("ok"))
            self.assertEqual(1, enq.get("queue_depth"))

            with patch("core_memory.integrations.openclaw_compaction_queue.process_compaction_event") as proc:
                proc.return_value = {"ok": True}
                out = drain_compaction_queue(root=td, max_items=1)
                self.assertTrue(out.get("ok"))
                self.assertEqual(1, out.get("processed"))
                self.assertEqual(0, out.get("queue_depth"))

    def test_failure_schedules_retry(self):
        with tempfile.TemporaryDirectory() as td:
            enqueue_compaction_event(event={"runId": "r1"}, ctx={"sessionKey": "main"}, root=td)
            with patch("core_memory.integrations.openclaw_compaction_queue.process_compaction_event") as proc:
                proc.return_value = {"ok": False, "error": "timeout"}
                out1 = drain_compaction_queue(root=td, max_items=1)
                self.assertGreaterEqual((out1.get("failed") or 0), 1)
                self.assertEqual(1, out1.get("queue_depth"))
                # immediate second drain should usually skip retry due backoff window
                out2 = drain_compaction_queue(root=td, max_items=1)
                self.assertEqual(0, out2.get("processed"))


if __name__ == "__main__":
    unittest.main()
