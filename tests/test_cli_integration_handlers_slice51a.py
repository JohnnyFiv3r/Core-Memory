from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core_memory.cli_handlers_integrations import handle_integration_commands


class TestCliIntegrationHandlersSlice51A(unittest.TestCase):
    def test_returns_false_for_non_integration_commands(self):
        args = SimpleNamespace(command="memory")
        handled = handle_integration_commands(args=args, memory=Mock(), sidecar_parser=Mock(), openclaw_parser=Mock())
        self.assertFalse(handled)

    def test_openclaw_unknown_subcommand_prints_help(self):
        args = SimpleNamespace(command="openclaw", openclaw_cmd="unknown")
        parser = Mock()
        handled = handle_integration_commands(args=args, memory=Mock(), sidecar_parser=Mock(), openclaw_parser=parser)
        self.assertTrue(handled)
        parser.print_help.assert_called_once()

    def test_openclaw_onboard_success(self):
        args = SimpleNamespace(
            command="openclaw",
            openclaw_cmd="onboard",
            openclaw_bin="openclaw",
            plugin_dir=None,
            replace_memory_core=False,
            dry_run=True,
        )
        with patch("core_memory.cli_handlers_integrations.run_openclaw_onboard", return_value={"ok": True}) as run, patch(
            "core_memory.cli_handlers_integrations.render_onboard_report", return_value="ok"
        ) as render, patch("builtins.print"):
            handled = handle_integration_commands(args=args, memory=Mock(), sidecar_parser=Mock(), openclaw_parser=Mock())

        self.assertTrue(handled)
        run.assert_called_once()
        render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
