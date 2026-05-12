from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MCPMountStaticTests(unittest.TestCase):
    def test_pyproject_declares_mcp_extra(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('mcp = ["fastapi", "uvicorn", "httpx", "mcp>=1.27.1,<2"]', text)

    def test_http_server_mounts_mcp_subapp(self):
        text = (ROOT / "core_memory/integrations/http/server.py").read_text()
        self.assertIn("from core_memory.integrations.mcp.protocol_server import MCP_HTTP_PATH, build_mcp_app", text)
        self.assertIn("return build_mcp_app()", text)
        self.assertIn("_mcp_app = _build_mcp_subapp()", text)
        self.assertIn("app.mount(MCP_HTTP_PATH, _mcp_app)", text)
        self.assertIn("mcp_session_manager", text)
        self.assertIn('@app.post("/v1/mcp/query-current-state")', text, "existing REST MCP endpoint should remain")

    def test_protocol_server_defines_health_endpoint_without_budget_language(self):
        text = (ROOT / "core_memory/integrations/mcp/protocol_server.py").read_text()
        constants = (ROOT / "core_memory/integrations/mcp/constants.py").read_text()
        self.assertIn('MCP_HTTP_PATH = "/mcp"', constants)
        self.assertIn('MCP_HEALTH_PATH = "/healthz"', constants)
        self.assertIn("async def mcp_healthz", text)
        self.assertNotRegex(text, re.compile(r"budget", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
