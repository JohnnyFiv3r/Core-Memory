#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.integrations.openclaw_runtime import coordinator_finalize_hook, finalize_and_process_turn
from core_memory.runtime.worker import SidecarPolicy
from core_memory.persistence.store import MemoryStore


class TestOpenClawIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-oc-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finalize_emit_and_canonical_turn_process(self):
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

        proc = finalize_and_process_turn(
            root=self.tmp,
            session_id="s1",
            turn_id="t2",
            transaction_id="tx2",
            trace_id="tr2",
            user_query="remember this canonical flow",
            assistant_final="Decision: canonical in-process path",
            policy=SidecarPolicy(create_threshold=0.6),
        )
        self.assertTrue(proc.get("ok"))
        self.assertEqual("canonical_in_process", proc.get("authority_path"))

        proc2 = finalize_and_process_turn(
            root=self.tmp,
            session_id="s1",
            turn_id="t2",
            transaction_id="tx2",
            trace_id="tr2",
            user_query="remember this canonical flow",
            assistant_final="Decision: canonical in-process path",
            policy=SidecarPolicy(create_threshold=0.6),
        )
        self.assertEqual(0, proc2.get("processed"))


if __name__ == "__main__":
    unittest.main()
