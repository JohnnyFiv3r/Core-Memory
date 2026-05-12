import tempfile
import unittest
from unittest.mock import patch

from core_memory.integrations.mcp.registry import TOOLS, call_tool
from core_memory.integrations.mcp.tools.capture import capture_handler


class MCPCaptureHandlerTests(unittest.TestCase):
    def test_capture_schema_is_wired_in_registry(self):
        tool = TOOLS["capture"]
        self.assertIs(tool.handler, capture_handler)
        self.assertIn("user", tool.input_schema["properties"])
        self.assertIn("turns", tool.input_schema["properties"])
        self.assertIn("bead_ids", tool.output_schema["properties"])

    def test_capture_rejects_missing_input_and_mixed_shapes(self):
        missing = capture_handler({})
        self.assertFalse(missing["ok"])
        self.assertEqual("cm.invalid_turn", missing["error"]["code"])
        mixed = capture_handler({"turns": [], "user": "hi"})
        self.assertFalse(mixed["ok"])
        self.assertEqual("cm.invalid_turn", mixed["error"]["code"])

    def test_capture_shortcut_calls_memory_capture(self):
        fake_result = {"ok": True, "session_id": "s", "turn_id": "t", "created": [{"bead_id": "bead-1"}]}
        with patch("core_memory.integrations.mcp.tools.capture.Memory") as memory_cls:
            memory_cls.return_value.capture.return_value = fake_result
            out = capture_handler({"root": "/tmp/cm", "session_id": "s", "turn_id": "t", "user": "hello", "assistant": "hi"})
        self.assertTrue(out["ok"])
        self.assertEqual(["bead-1"], out["bead_ids"])
        memory_cls.assert_called_once_with(root="/tmp/cm")
        memory_cls.return_value.capture.assert_called_once()

    def test_call_tool_capture_smoke_with_real_store(self):
        with tempfile.TemporaryDirectory() as td:
            out = call_tool("capture", {"root": td, "session_id": "s", "turn_id": "t", "user": "hello", "assistant": "hi"})
        self.assertTrue(out["ok"])
        self.assertEqual("s", out["session_id"])
        self.assertEqual("t", out["turn_id"])


if __name__ == "__main__":
    unittest.main()
