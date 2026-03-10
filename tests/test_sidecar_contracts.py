#!/usr/bin/env python3

import shutil
import tempfile
import unittest
from pathlib import Path

from core_memory.event_state import TurnEnvelope, emit_memory_event, memory_pass_key, mark_memory_pass, get_memory_pass


class TestSidecarContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-sidecar-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_envelope_hashes_and_emit(self):
        env = TurnEnvelope(
            session_id="s1",
            turn_id="t1",
            transaction_id="x1",
            trace_id="tr1",
            user_query="why",
            assistant_final="because",
        )
        event = emit_memory_event(self.tmp, env)
        self.assertTrue(event.event_id.startswith("mev-"))
        self.assertTrue(env.assistant_final_hash)
        self.assertTrue(env.envelope_hash)

    def test_memory_pass_state(self):
        mark_memory_pass(self.tmp, "s1", "t1", "done", "abc")
        st = get_memory_pass(self.tmp, "s1", "t1")
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["envelope_hash"], "abc")
        self.assertEqual(memory_pass_key("s1", "t1"), "s1:t1")


if __name__ == "__main__":
    unittest.main()
