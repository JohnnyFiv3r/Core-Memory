#!/usr/bin/env python3

import os
import shutil
import tempfile
import unittest

from core_memory.openclaw_integration import finalize_and_process_turn
from core_memory.runtime.worker import SidecarPolicy
from core_memory.store import MemoryStore


class TestE2ESidecarPerTurn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-e2e-sidecar-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bead_written_per_turn_with_finalize_process_flow(self):
        """Test beads are created when using canonical finalize_and_process_turn path."""
        turns = [
            ("t1", "Remember this decision", "Decision: always use stdlib for reliability"),
            ("t2", "Remember this lesson", "Lesson: avoid overfitting to one environment"),
            ("t3", "Remember this evidence", "Evidence: logs show reduced failure rate"),
        ]

        for turn_id, uq, af in turns:
            out = finalize_and_process_turn(
                root=self.tmp,
                session_id="s-main",
                turn_id=turn_id,
                transaction_id=f"tx-{turn_id}",
                trace_id=f"tr-{turn_id}",
                user_query=uq,
                assistant_final=af,
                trace_depth=0,
                origin="USER_TURN",
                policy=SidecarPolicy(create_threshold=0.5),
            )
            self.assertTrue(out.get("ok"), out)

        # Verify beads were created via stats (canonical path populates stats correctly)
        stats = self.store.stats()
        self.assertGreaterEqual(stats["total_beads"], len(turns))


if __name__ == "__main__":
    unittest.main()
