from __future__ import annotations

import unittest
from pathlib import Path


class TestCliOpsSplitSlice52A(unittest.TestCase):
    def test_cli_routes_async_ops_through_extracted_parser_and_handler_modules(self):
        repo = Path(__file__).resolve().parents[1]
        cli_text = (repo / "core_memory" / "cli.py").read_text(encoding="utf-8")

        self.assertIn("from .cli_parser_ops import add_async_jobs_command_surfaces", cli_text)
        self.assertIn("from .cli_handlers_ops import handle_ops_commands", cli_text)
        self.assertIn("add_async_jobs_command_surfaces(", cli_text)
        self.assertIn("elif handle_ops_commands(args=args, memory=memory):", cli_text)


if __name__ == "__main__":
    unittest.main()
