import pathlib
import unittest


class TestPypiMcpVerificationArtifacts(unittest.TestCase):
    def test_verification_script_covers_required_smoke_steps(self):
        text = pathlib.Path("scripts/verify_pypi_mcp.py").read_text()
        self.assertIn("build", text)
        self.assertIn("core-memory", text)
        self.assertIn("mcp", text)
        self.assertIn("version", text)
        self.assertIn("serve", text)
        self.assertIn("initialize", text)
        self.assertIn("list_tools", text)

    def test_local_first_quickstart_documents_required_examples(self):
        text = pathlib.Path("docs/adoption/local-first-quickstart.md").read_text()
        for needle in ["No hosted", "Ollama", "LM Studio", "vLLM", "OpenRouter", "MCP client", "Troubleshooting"]:
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
