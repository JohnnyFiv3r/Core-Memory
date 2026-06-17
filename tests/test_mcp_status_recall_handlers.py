from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MCPStatusRecallHandlerStaticTests(unittest.TestCase):
    def test_status_handler_shape_uses_store_stats(self):
        text = (ROOT / "core_memory/integrations/mcp/tools/status.py").read_text()
        self.assertIn("def status_handler", text)
        self.assertIn("MemoryStore", text)
        for key in [
            "beads_total",
            "sessions_total",
            "last_capture_at",
            "last_snapshot_id",
            "last_snapshot_source",
            "last_snapshot_conversation_id",
            "queue_depth",
            "tools_version",
            "schema_version",
            "advertised_tools_count",
            "writable",
            "connected_adapters",
            "mcp_version",
            "server_version",
        ]:
            self.assertIn(key, text)

    def test_recall_handler_uses_effort_not_budget(self):
        # The payload/effort logic lives in the shared wire-surface module
        # (core_memory/integrations/recall_payload.py); the MCP tool delegates.
        tool_text = (ROOT / "core_memory/integrations/mcp/tools/recall.py").read_text()
        self.assertIn("def recall_handler", tool_text)
        self.assertIn("run_recall_payload", tool_text)
        self.assertNotIn("budget", tool_text.lower())
        shared_text = (ROOT / "core_memory/integrations/recall_payload.py").read_text()
        self.assertIn("validate_recall_effort", shared_text)
        self.assertIn("effort=effort", shared_text)
        self.assertIn("effort='dynamic' is reserved", shared_text)
        self.assertNotIn("budget", shared_text.lower())

    def test_registry_wires_status_and_recall_handlers(self):
        text = (ROOT / "core_memory/integrations/mcp/registry.py").read_text()
        self.assertIn("from core_memory.integrations.mcp.tools.recall import recall_handler", text)
        self.assertIn("from core_memory.integrations.mcp.tools.status import status_handler", text)
        self.assertIn("handler=recall_handler", text)
        self.assertIn("handler=status_handler", text)
        self.assertIn('"effort": {"enum": ["low", "medium", "high"]}', text)
        self.assertNotIn('"budget"', text)


if __name__ == "__main__":
    unittest.main()
