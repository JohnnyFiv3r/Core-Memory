import asyncio
import socket
import tempfile
import threading
import time
import unittest

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core_memory.integrations.mcp.protocol_server import build_mcp_app


class MCPProtocolClientSmokeTests(unittest.TestCase):
    def test_streamable_http_client_lists_tools_prompt_and_calls_status(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with tempfile.TemporaryDirectory() as td:
            app = build_mcp_app(root=td)
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                deadline = time.time() + 10
                while not server.started and time.time() < deadline:
                    time.sleep(0.05)
                self.assertTrue(server.started)

                async def run_client():
                    async with streamable_http_client(f"http://127.0.0.1:{port}/") as (read, write, _get_session_id):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            prompts = await session.list_prompts()
                            status = await session.call_tool("status", {})
                            return tools, prompts, status

                tools, prompts, status = asyncio.run(run_client())
            finally:
                server.should_exit = True
                thread.join(timeout=5)

        self.assertEqual(["capture", "recall", "ingest", "status"], [tool.name for tool in tools.tools])
        self.assertIn("core-memory.agent-guide", [prompt.name for prompt in prompts.prompts])
        self.assertFalse(status.isError)
        self.assertTrue(status.structuredContent["ok"])
        self.assertEqual("1.27.1", status.structuredContent["mcp_version"])


if __name__ == "__main__":
    unittest.main()
