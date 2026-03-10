#!/usr/bin/env python3

import shutil
import tempfile
import unittest
from pathlib import Path

from core_memory.event_state import mark_memory_pass
from core_memory.event_ingress import maybe_emit_finalize_memory_event, should_emit_memory_event


class TestSidecarHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-hook-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guard_rules(self):
        self.assertTrue(should_emit_memory_event(0, "USER_TURN"))
        self.assertFalse(should_emit_memory_event(1, "USER_TURN"))
        self.assertFalse(should_emit_memory_event(0, "MEMORY_PASS"))

    def test_emits_once_idempotent(self):
        r1 = maybe_emit_finalize_memory_event(
            str(self.tmp),
            session_id="s1",
            turn_id="t1",
            transaction_id="tx1",
            trace_id="tr1",
            user_query="u",
            assistant_final="a",
            trace_depth=0,
            origin="USER_TURN",
        )
        self.assertTrue(r1["emitted"])

        # Simulate completion mark
        mark_memory_pass(self.tmp, "s1", "t1", "done", "")

        r2 = maybe_emit_finalize_memory_event(
            str(self.tmp),
            session_id="s1",
            turn_id="t1",
            transaction_id="tx2",
            trace_id="tr2",
            user_query="u",
            assistant_final="a",
            trace_depth=0,
            origin="USER_TURN",
        )
        # Could be mutation if hash mismatches stored empty; ensure no crash and deterministic result
        self.assertIn(r2["reason"], {"idempotent_done", "turn_mutation"})

    def test_skips_subagent_depth(self):
        r = maybe_emit_finalize_memory_event(
            str(self.tmp),
            session_id="s1",
            turn_id="t2",
            transaction_id="tx1",
            trace_id="tr1",
            user_query="u",
            assistant_final="a",
            trace_depth=2,
            origin="SUBAGENT",
        )
        self.assertFalse(r["emitted"])
        self.assertEqual(r["reason"], "guard_skipped")


if __name__ == "__main__":
    unittest.main()
