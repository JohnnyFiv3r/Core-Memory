from __future__ import annotations

import argparse
import unittest

from core_memory.cli_parser_extended import (
    add_sidecar_openclaw_parsers,
    add_graph_parser,
    add_metrics_parser,
)


class TestCliParserExtendedSlice51A(unittest.TestCase):
    def _base(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        return parser, sub

    def test_add_sidecar_openclaw_parsers(self):
        parser, sub = self._base()
        sidecar, openclaw = add_sidecar_openclaw_parsers(sub, legacy_help="legacy")
        self.assertIsNotNone(sidecar)
        self.assertIsNotNone(openclaw)

        args = parser.parse_args(["sidecar", "finalize", "--session-id", "s1", "--turn-id", "t1", "--user-query", "u", "--assistant-final", "a"])
        self.assertEqual("sidecar", args.command)
        self.assertEqual("finalize", args.sidecar_cmd)

    def test_add_graph_parser(self):
        parser, sub = self._base()
        g = add_graph_parser(sub, legacy_help="legacy")
        self.assertIsNotNone(g)

        args = parser.parse_args(["graph", "semantic-lookup", "--query", "redis", "--k", "3"])
        self.assertEqual("graph", args.command)
        self.assertEqual("semantic-lookup", args.graph_cmd)
        self.assertEqual("redis", args.query)
        self.assertEqual(3, args.k)

    def test_add_metrics_parser(self):
        parser, sub = self._base()
        m = add_metrics_parser(sub, legacy_help="legacy")
        self.assertIsNotNone(m)

        args = parser.parse_args(["metrics", "report", "--since", "48h"])
        self.assertEqual("metrics", args.command)
        self.assertEqual("report", args.metrics_cmd)
        self.assertEqual("48h", args.since)

    def test_add_metrics_parser_dreamer_and_longitudinal(self):
        parser, sub = self._base()
        _m = add_metrics_parser(sub, legacy_help="legacy")

        args1 = parser.parse_args(["metrics", "dreamer-eval", "--since", "30d", "--strict"])
        self.assertEqual("dreamer-eval", args1.metrics_cmd)
        self.assertTrue(args1.strict)

        args2 = parser.parse_args(["metrics", "longitudinal-benchmark-v2", "--since", "14d"])
        self.assertEqual("longitudinal-benchmark-v2", args2.metrics_cmd)
        self.assertEqual("14d", args2.since)


if __name__ == "__main__":
    unittest.main()
