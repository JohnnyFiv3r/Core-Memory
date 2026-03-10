#!/usr/bin/env python3

import os
import shutil
import tempfile
import unittest

from core_memory.openclaw_integration import coordinator_finalize_hook, process_pending_memory_events
from core_memory.event_worker import SidecarPolicy
from core_memory.store import MemoryStore


class TestOpenClawIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-oc-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finalize_emit_and_process(self):
        old = os.environ.get("CORE_MEMORY_ENABLE_LEGACY_POLLER")
        try:
            os.environ["CORE_MEMORY_ENABLE_LEGACY_POLLER"] = "1"

            out = coordinator_finalize_hook(
                root=self.tmp,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this decision",
                assistant_final="Decision: use stdlib for safety",
                trace_depth=0,
                origin="USER_TURN",
                window_bead_ids=[],
            )
            self.assertTrue(out.get("emitted"))

            proc = process_pending_memory_events(self.tmp, max_events=10, policy=SidecarPolicy(create_threshold=0.6))
            self.assertGreaterEqual(proc["processed"], 1)

            # idempotent: re-processing same queue should not create duplicates
            proc2 = process_pending_memory_events(self.tmp, max_events=10, policy=SidecarPolicy(create_threshold=0.6))
            self.assertEqual(proc2["processed"], 0)

            stats = self.store.stats()
            self.assertEqual(0, stats["total_beads"])
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_ENABLE_LEGACY_POLLER", None)
            else:
                os.environ["CORE_MEMORY_ENABLE_LEGACY_POLLER"] = old


if __name__ == "__main__":
    unittest.main()
