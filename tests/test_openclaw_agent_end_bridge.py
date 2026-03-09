import tempfile
import unittest

from core_memory.integrations.openclaw_agent_end_bridge import process_agent_end_event


class TestOpenClawAgentEndBridge(unittest.TestCase):
    def test_emits_once_and_dedupes(self):
        with tempfile.TemporaryDirectory(prefix="cm-bridge-") as td:
            event = {
                "messages": [
                    {"role": "user", "content": "remember that we chose event-driven finalize"},
                    {"role": "assistant", "content": "Confirmed. We will use agent_end thin bridge."},
                ],
                "success": True,
                "runId": "run-1",
            }
            ctx = {"sessionId": "s1", "sessionKey": "main", "agentId": "main"}

            out1 = process_agent_end_event(event=event, ctx=ctx, root=td)
            self.assertTrue(out1.get("ok"))
            self.assertTrue(out1.get("emitted"))
            self.assertTrue(out1.get("event_id"))

            out2 = process_agent_end_event(event=event, ctx=ctx, root=td)
            self.assertTrue(out2.get("ok"))
            self.assertFalse(out2.get("emitted"))
            self.assertEqual(out2.get("reason"), "deduped")

    def test_skips_memory_trigger(self):
        with tempfile.TemporaryDirectory(prefix="cm-bridge-") as td:
            event = {
                "messages": [
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
            }
            ctx = {"sessionId": "s1", "sessionKey": "main", "trigger": "memory"}
            out = process_agent_end_event(event=event, ctx=ctx, root=td)
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("emitted"))
            self.assertEqual(out.get("reason"), "memory_trigger_skip")


if __name__ == "__main__":
    unittest.main()
