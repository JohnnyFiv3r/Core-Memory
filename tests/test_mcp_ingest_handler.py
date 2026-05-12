import json
import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.mcp.registry import call_tool
from core_memory.integrations.mcp.tools.ingest import ingest_handler


class MCPIngestHandlerTests(unittest.TestCase):
    def test_ingests_json_messages_through_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            transcript = Path(td) / "chat.json"
            transcript.write_text(json.dumps({"messages": [
                {"role": "user", "content": "Remember that the build uses MCP 1.27.1."},
                {"role": "assistant", "content": "Noted; MCP is pinned below 2."},
            ]}), encoding="utf-8")
            out = ingest_handler({"root": str(root), "path": str(transcript), "from": "json", "session_prefix": "test"})
        self.assertTrue(out["ok"])
        self.assertEqual("json", out["format"])
        self.assertEqual(2, out["turns_ingested"])
        self.assertEqual("test:chat", out["session_id"])
        self.assertIn("raw", out)

    def test_ingests_markdown_speaker_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            transcript = Path(td) / "chat.md"
            transcript.write_text("User: hello\ncontinued\n\nAssistant: hi there\n", encoding="utf-8")
            out = call_tool("ingest", {"root": str(root), "path": str(transcript)})
        self.assertTrue(out["ok"])
        self.assertEqual("markdown", out["format"])
        self.assertEqual(2, out["turns_ingested"])

    def test_rejects_unreadable_path(self):
        out = ingest_handler({"path": "/definitely/not/here.json"})
        self.assertFalse(out["ok"])
        self.assertEqual("cm.path_not_readable", out["error"]["code"])

    def test_rejects_transcript_without_user_assistant_shape(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "bad.json"
            transcript.write_text(json.dumps([{"role": "system", "content": "rules"}]), encoding="utf-8")
            out = ingest_handler({"path": str(transcript), "from": "json"})
        self.assertFalse(out["ok"])
        self.assertEqual("cm.parser_aborted", out["error"]["code"])

    def test_registry_exposes_real_ingest_handler(self):
        out = call_tool("ingest", {})
        self.assertFalse(out["ok"])
        self.assertNotEqual("mcp_tool_not_implemented", out.get("error"))
        self.assertEqual("cm.path_not_readable", out["error"]["code"])


if __name__ == "__main__":
    unittest.main()
