#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.sidecar_worker import process_memory_event, SidecarPolicy
from core_memory.store import MemoryStore


class TestSidecarWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-worker-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_noop_allowed_when_low_signal(self):
        payload = {
            "envelope": {
                "session_id": "s1",
                "turn_id": "t1",
                "user_query": "hi",
                "assistant_final": "hello",
                "window_bead_ids": [],
                "envelope_hash": "h1",
            }
        }
        delta = process_memory_event(self.tmp, payload, policy=SidecarPolicy(create_threshold=0.95))
        self.assertEqual(len(delta["created"]), 0)
        self.assertEqual(len(delta["promoted"]), 0)

    def test_budget_create_and_promotion_preview_only(self):
        b = self.store.add_bead(type="context", title="seed", summary=["x"], session_id="s1")
        payload = {
            "envelope": {
                "session_id": "s1",
                "turn_id": "t2",
                "user_query": "remember this decision",
                "assistant_final": "Important decision: always do X",
                "window_bead_ids": [b],
                "envelope_hash": "h2",
            }
        }
        delta = process_memory_event(self.tmp, payload, policy=SidecarPolicy())
        self.assertLessEqual(len(delta["created"]), 1)
        self.assertEqual(0, len(delta["promoted"]))
        self.assertTrue(all(c.get("authoritative") is False for c in (delta.get("promotion_candidates") or [])))


if __name__ == "__main__":
    unittest.main()
