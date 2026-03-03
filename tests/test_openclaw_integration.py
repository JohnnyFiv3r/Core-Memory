#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.openclaw_integration import coordinator_finalize_hook, process_pending_memory_events
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.store import MemoryStore


class TestOpenClawIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-oc-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finalize_emit_and_process(self):
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

        stats = self.store.stats()
        self.assertGreaterEqual(stats["total_beads"], 1)


if __name__ == "__main__":
    unittest.main()
