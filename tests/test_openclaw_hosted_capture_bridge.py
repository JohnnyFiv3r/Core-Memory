from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.integrations.openclaw.hosted_capture_bridge import process_hosted_capture_event


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"ok": True, "accepted": True, "event_id": "mev-hosted", "processed": 1}).encode()


class TestOpenClawHostedCaptureBridge(unittest.TestCase):
    def test_posts_turn_finalized_payload_and_dedupes(self):
        with tempfile.TemporaryDirectory(prefix="cm-hosted-bridge-") as td:
            state_path = str(Path(td) / "hosted-state.json")
            captured = {}

            def fake_urlopen(req, timeout=0):  # noqa: ANN001
                captured["url"] = req.full_url
                captured["timeout"] = timeout
                captured["headers"] = dict(req.header_items())
                captured["body"] = json.loads(req.data.decode("utf-8"))
                return _FakeResponse()

            event = {
                "messages": [
                    {"role": "user", "content": "Satorid connection part 4 test"},
                    {"role": "assistant", "content": "Satorid connection test OK"},
                ],
                "success": True,
                "runId": "run-hosted-1",
            }
            ctx = {"sessionId": "agent:main:main", "sessionKey": "agent:main:main", "agentId": "main"}

            with patch("urllib.request.urlopen", fake_urlopen):
                out1 = process_hosted_capture_event(
                    event=event,
                    ctx=ctx,
                    hosted={
                        "url": "https://app.satorid.ai/agent-gateway/openclaw/core-memory/turn-finalized",
                        "token": "sgw_test_secret",
                        "statePath": state_path,
                    },
                )
                out2 = process_hosted_capture_event(
                    event=event,
                    ctx=ctx,
                    hosted={
                        "url": "https://app.satorid.ai/agent-gateway/openclaw/core-memory/turn-finalized",
                        "token": "sgw_test_secret",
                        "statePath": state_path,
                    },
                )

            self.assertTrue(out1.get("ok"))
            self.assertTrue(out1.get("emitted"))
            self.assertEqual("mev-hosted", out1.get("event_id"))
            self.assertEqual("https://app.satorid.ai/agent-gateway/openclaw/core-memory/turn-finalized", captured["url"])
            self.assertEqual("Bearer sgw_test_secret", captured["headers"].get("Authorization"))
            self.assertEqual("OPENCLAW_HOSTED_CLONE", captured["body"]["origin"])
            self.assertEqual("agent:main:main", captured["body"]["session_id"])
            self.assertEqual("Satorid connection part 4 test", captured["body"]["turns"][0]["content"])
            self.assertEqual("Satorid connection test OK", captured["body"]["turns"][1]["content"])
            self.assertTrue(captured["body"]["metadata"]["local_core_memory_bypassed"])
            self.assertTrue(out2.get("ok"))
            self.assertFalse(out2.get("emitted"))
            self.assertEqual("deduped", out2.get("reason"))

    def test_skips_missing_assistant_output(self):
        out = process_hosted_capture_event(
            event={"messages": [{"role": "user", "content": "hello"}]},
            ctx={"sessionId": "s1"},
            hosted={"url": "https://app.satorid.ai/agent-gateway/openclaw/core-memory/turn-finalized"},
        )
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("emitted"))
        self.assertEqual("missing_assistant_output", out.get("reason"))


if __name__ == "__main__":
    unittest.main()
