import unittest

from fastapi.testclient import TestClient

from core_memory.integrations.mcp.protocol_server import build_mcp_app


class MCPProtocolServerLiveTests(unittest.TestCase):
    def test_healthz_reports_tools_and_prompt(self):
        app = build_mcp_app(root="/tmp/core-memory-test")
        with TestClient(app) as client:
            res = client.get("/healthz")
        self.assertEqual(200, res.status_code)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual("/tmp/core-memory-test", data["root"])
        self.assertEqual(["capture", "ingest", "recall", "status"], data["tools"])
        self.assertEqual("core-memory.agent-guide", data["prompt"])

    def test_streamable_http_endpoint_lifespan_is_initialized(self):
        app = build_mcp_app()
        with TestClient(app) as client:
            res = client.get("/")
        # GET without MCP session headers is not a valid MCP request, but it should
        # reach the SDK endpoint instead of failing with an uninitialized task group.
        self.assertIn(res.status_code, {400, 405, 406, 421})
        self.assertNotIn("Task group is not initialized", res.text)


if __name__ == "__main__":
    unittest.main()
