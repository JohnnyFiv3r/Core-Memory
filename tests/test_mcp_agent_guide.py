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
        self.assertEqual({"capture", "recall", "capture_session", "ingest", "status"}, set(descriptions))
        self.assertIn('effort="low"', descriptions["recall"])
        self.assertIn("canonical write boundary", descriptions["capture"])
        self.assertIn("safety net", descriptions["capture_session"])
        for value in descriptions.values():
            self.assertGreater(len(value.split()), 10)

    def test_registry_uses_guide_descriptions(self):
        self.assertEqual(tool_description("recall"), TOOLS["recall"].description)
        self.assertIn("single public read verb", TOOLS["recall"].description)


if __name__ == "__main__":
    unittest.main()
