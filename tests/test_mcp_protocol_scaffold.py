import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MCPProtocolScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent_guide = load_module("mcp_agent_guide_scaffold", "core_memory/integrations/mcp/agent_guide.py")
        cls.errors = load_module("mcp_errors_scaffold", "core_memory/integrations/mcp/errors.py")
        cls.registry_text = (ROOT / "core_memory/integrations/mcp/registry.py").read_text()

    def test_v1_tool_registry_names_and_effort_schema(self):
        for name in ["capture", "recall", "ingest", "maintain", "status"]:
            self.assertIn(f'"{name}": MCPToolDefinition', self.registry_text)
        self.assertIn('"effort": {"enum": ["low", "medium", "high"]}', self.registry_text)
        self.assertIn('"hints": {', self.registry_text)
        self.assertIn('"causal_labels"', self.registry_text)
        self.assertNotIn('"budget"', self.registry_text)

    def test_error_codes_use_effort_naming(self):
        self.assertIn("cm.recall_effort_exhausted", self.errors.ERROR_CODES)
        self.assertNotIn("cm.recall_budget_exhausted", self.errors.ERROR_CODES)
        err = self.errors.CoreMemoryMCPError("cm.store_not_found", "missing", {"root": "/tmp/missing"})
        self.assertEqual("cm.store_not_found", err.to_dict()["error"]["code"])

    def test_agent_guide_scaffold_prompt_and_fallbacks(self):
        self.assertEqual("core-memory.agent-guide", self.agent_guide.PROMPT_NAME)
        self.assertIn("effort", self.agent_guide.fallback_tool_description("recall"))
        self.assertIn("def list_tools", self.registry_text)


if __name__ == "__main__":
    unittest.main()
