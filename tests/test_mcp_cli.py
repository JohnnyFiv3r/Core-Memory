import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from core_memory.integrations.mcp.cli import SEMANTIC_INSTALL_WARNING, install_client_config, install_payload, status_payload, version_payload


class MCPCLITests(unittest.TestCase):
    def test_version_payload_reports_pinned_sdk(self):
        data = version_payload()
        self.assertTrue(data["ok"])
        self.assertEqual("1.27.1", data["mcp_spec_version"])
        self.assertEqual("mcp", data["mcp_sdk_package"])
        self.assertRegex(data["mcp_sdk_version"], r"^1\.27\.1$|not-installed")

    def test_status_payload_handles_server_down(self):
        data = status_payload(port=9, timeout=0.1)
        self.assertFalse(data["ok"])
        self.assertIn("http://localhost:9/mcp", data["url"])

    def test_install_client_config_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / ".cursor" / "mcp.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"other": True, "mcpServers": {"x": {"url": "http://x"}}}), encoding="utf-8")
            with mock.patch("core_memory.integrations.mcp.cli._home", return_value=Path(td)):
                out = install_client_config("cursor", port=8123)
            self.assertTrue(out["ok"])
            data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertTrue(data["other"])
        self.assertEqual("http://x", data["mcpServers"]["x"]["url"])
        self.assertEqual("http://localhost:8123/mcp", data["mcpServers"]["core-memory"]["url"])

    def test_install_payload_no_detected_clients_returns_manual_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core_memory.integrations.mcp.cli._home", return_value=Path(td)):
                out = install_payload(no_start=True, dry_run=True)
        self.assertFalse(out["ok"])
        self.assertEqual("no_clients_detected", out["error"]["code"])
        self.assertIn("core-memory", out["manual"]["mcpServers"])

    def test_install_payload_includes_semantic_doctor_hint(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / ".cursor" / "mcp.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({}), encoding="utf-8")
            root = Path(td) / "store"
            stderr = StringIO()
            with mock.patch("core_memory.integrations.mcp.cli._home", return_value=Path(td)), \
                 mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch("sys.stderr", stderr):
                out = install_payload(client="cursor", root=str(root), no_start=True, dry_run=True)
        self.assertTrue(out["ok"])
        self.assertIn("semantic", out)
        self.assertIn("next_step", out["semantic"])
        self.assertIn(out["semantic"]["mode"], {"required", "degraded_allowed"})
        self.assertEqual(SEMANTIC_INSTALL_WARNING + "\n", stderr.getvalue())

    def test_cli_mcp_version_command_outputs_json(self):
        proc = subprocess.run(
            [sys.executable, "-m", "core_memory.cli", "mcp", "version"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            text=True,
            capture_output=True,
        )
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual("1.27.1", data["mcp_spec_version"])


if __name__ == "__main__":
    unittest.main()
