import unittest

from core_memory.integrations.mcp.agent_guide import PROMPT_NAME, load_agent_guide, tool_description, tool_descriptions
from core_memory.integrations.mcp.registry import TOOLS


class MCPAgentGuideTests(unittest.TestCase):
    def test_agent_guide_loads_packaged_markdown(self):
        guide = load_agent_guide()
        self.assertIn("# Core Memory Agent Guide", guide)
        self.assertIn("<!-- tool:recall:start -->", guide)
        self.assertEqual("core-memory.agent-guide", PROMPT_NAME)

    def test_tool_descriptions_extract_named_sections(self):
        descriptions = tool_descriptions()
        self.assertEqual(
            {"capture", "recall", "capture_session", "sync_transcript_snapshot", "ingest", "maintain", "status"},
            set(descriptions),
        )
        self.assertIn('effort="low"', descriptions["recall"])
        self.assertIn("canonical write boundary", descriptions["capture"])
        self.assertIn("safety net", descriptions["capture_session"])
        self.assertIn("visible conversation", descriptions["sync_transcript_snapshot"])
        self.assertIn("required safety net", descriptions["sync_transcript_snapshot"])
        self.assertIn("user_opted_in=true", descriptions["sync_transcript_snapshot"])
        self.assertIn("control-plane", descriptions["maintain"])
        self.assertIn("writable", descriptions["status"])
        self.assertIn("last transcript snapshot", descriptions["status"])
        for value in descriptions.values():
            self.assertGreater(len(value.split()), 10)

    def test_registry_uses_guide_descriptions(self):
        self.assertEqual(tool_description("recall"), TOOLS["recall"].description)
        self.assertIn("single public read verb", TOOLS["recall"].description)

    def test_registry_advertises_chat_client_tool_findings(self):
        status = TOOLS["status"]
        self.assertEqual("Core Memory Status", status.title)
        self.assertEqual(
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            status.annotations,
        )

        sync = TOOLS["sync_transcript_snapshot"]
        self.assertEqual("Sync Transcript Snapshot", sync.title)
        self.assertEqual(False, sync.annotations["readOnlyHint"])
        self.assertEqual(False, sync.annotations["destructiveHint"])
        self.assertEqual(True, sync.annotations["idempotentHint"])
        self.assertIn("idempotency_key", sync.input_schema["properties"])
        self.assertIn("duplicate", sync.output_schema["properties"])
        self.assertIn("snapshot_id", sync.output_schema["properties"])


if __name__ == "__main__":
    unittest.main()
