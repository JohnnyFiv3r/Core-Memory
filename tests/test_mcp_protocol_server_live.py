import unittest

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed; skipping MCP server live tests")
from fastapi.testclient import TestClient  # noqa: E402

from core_memory.integrations.mcp.protocol_server import _transport_security_settings, build_mcp_app  # noqa: E402


class MCPProtocolServerLiveTests(unittest.TestCase):
    def test_healthz_reports_tools_and_prompt(self):
        app = build_mcp_app(root="/tmp/core-memory-test")
        with TestClient(app) as client:
            res = client.get("/healthz")
        self.assertEqual(200, res.status_code)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual("/tmp/core-memory-test", data["root"])
        for name in [
            "capture",
            "ingest",
            "recall",
            "status",
            "query_current_state",
            "query_temporal_window",
            "write_turn_finalized",
            "apply_reviewed_proposal",
            "submit_entity_merge_proposal",
        ]:
            self.assertIn(name, data["tools"])
        self.assertEqual("core-memory.agent-guide", data["prompt"])

    def test_streamable_http_endpoint_lifespan_is_initialized(self):
        app = build_mcp_app()
        with TestClient(app) as client:
            res = client.get("/")
        # GET without MCP session headers is not a valid MCP request, but it should
        # reach the SDK endpoint instead of failing with an uninitialized task group.
        self.assertIn(res.status_code, {400, 405, 406, 421})
        self.assertNotIn("Task group is not initialized", res.text)

    def test_transport_security_allows_local_and_hosted_demo_hosts(self):
        settings = _transport_security_settings()
        self.assertTrue(settings.enable_dns_rebinding_protection)
        self.assertIn("127.0.0.1:*", settings.allowed_hosts)
        self.assertIn("core-memory-demo.onrender.com", settings.allowed_hosts)
        self.assertIn("demo.usecorememory.com", settings.allowed_hosts)
        self.assertIn("https://demo.usecorememory.com", settings.allowed_origins)


if __name__ == "__main__":
    unittest.main()
