from __future__ import annotations

import unittest
from types import SimpleNamespace

from core_memory.cli_compat import (
    rewrite_legacy_dev_memory_argv,
    ensure_group_subcommand_selected,
    apply_grouped_aliases,
)


class _DummyParser:
    def __init__(self):
        self.help_called = False

    def print_help(self):
        self.help_called = True


class TestCliCompatModuleSlice46B(unittest.TestCase):
    def test_rewrite_legacy_dev_memory_argv(self):
        self.assertEqual(
            ["--root", "./m", "memory", "search", "--query", "x"],
            rewrite_legacy_dev_memory_argv(["--root", "./m", "dev", "memory", "search", "--query", "x"]),
        )

    def test_group_subcommand_missing_prints_help(self):
        args = SimpleNamespace(command="setup", setup_cmd=None)
        setup = _DummyParser()
        done = ensure_group_subcommand_selected(
            args,
            {
                "setup": setup,
                "store": _DummyParser(),
                "recall": _DummyParser(),
                "inspect": _DummyParser(),
                "integrations": _DummyParser(),
                "ops": _DummyParser(),
                "dev": _DummyParser(),
            },
        )
        self.assertTrue(done)
        self.assertTrue(setup.help_called)

    def test_apply_grouped_aliases_maps_recall_search_to_memory_search(self):
        args = SimpleNamespace(command="recall", recall_cmd="search")
        done = apply_grouped_aliases(args, openclaw_group_parser=_DummyParser())
        self.assertFalse(done)
        self.assertEqual("memory", args.command)
        self.assertEqual("search", args.memory_cmd)

    def test_apply_grouped_aliases_maps_ops_graph_sync(self):
        args = SimpleNamespace(command="ops", ops_cmd="graph-sync")
        done = apply_grouped_aliases(args, openclaw_group_parser=_DummyParser())
        self.assertFalse(done)
        self.assertEqual("graph", args.command)
        self.assertEqual("sync-structural", args.graph_cmd)
        self.assertTrue(args.apply)
        self.assertFalse(args.strict)


if __name__ == "__main__":
    unittest.main()
