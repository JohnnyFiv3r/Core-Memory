from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
from pathlib import Path

from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates

from core_memory.cli_handlers_store import handle_store_commands
from core_memory.cli_handlers_graph import handle_graph_command
from core_memory.cli_handlers_metrics import handle_metrics_command


class TestCliHandlerModulesSlice51A(unittest.TestCase):
    def test_store_handler_routes_add(self):
        memory = Mock()
        memory.add_bead.return_value = "bead-1"
        args = SimpleNamespace(
            command="add",
            type="decision",
            title="t",
            summary=["s"],
            because=[],
            source_turn_ids=["t1"],
            tags=[],
            context_tags=[],
            session_id="s1",
        )
        with patch("builtins.print"):
            handled = handle_store_commands(args=args, memory=memory, doctor_report=lambda _r: {"ok": True})
        self.assertTrue(handled)
        memory.add_bead.assert_called_once()

    def test_graph_handler_unknown_prints_help(self):
        memory = Mock()
        parser = Mock()
        args = SimpleNamespace(command="graph", graph_cmd="unknown")
        handled = handle_graph_command(args=args, memory=memory, graph_parser=parser)
        self.assertTrue(handled)
        parser.print_help.assert_called_once()

    def test_metrics_handler_report(self):
        memory = Mock()
        memory.metrics_report.return_value = {"runs": 0}
        parser = Mock()
        args = SimpleNamespace(command="metrics", metrics_cmd="report", since="7d")
        with patch("builtins.print"):
            handled = handle_metrics_command(
                args=args,
                memory=memory,
                metrics_parser=parser,
                canonical_health_report=lambda root, write_path=None: {"ok": True},
            )
        self.assertTrue(handled)
        memory.metrics_report.assert_called_once_with(since="7d")

    def test_metrics_handler_longitudinal_benchmark_v2(self):
        with tempfile.TemporaryDirectory(prefix="cm-cli-handler-") as td:
            root = Path(td) / "memory"
            enqueue_dreamer_candidates(
                root=root,
                associations=[{"source": "b1", "target": "b2", "relationship": "reinforces", "novelty": 0.4, "grounding": 0.7}],
                run_metadata={"run_id": "r1"},
            )

            memory = Mock()
            memory.root = root
            parser = Mock()
            args = SimpleNamespace(command="metrics", metrics_cmd="longitudinal-benchmark-v2", since="30d", strict=False, write=None)
            with patch("builtins.print"):
                handled = handle_metrics_command(
                    args=args,
                    memory=memory,
                    metrics_parser=parser,
                    canonical_health_report=lambda root, write_path=None: {"ok": True},
                )
            self.assertTrue(handled)

    def test_metrics_handler_reviewer_quick_value_v2(self):
        with tempfile.TemporaryDirectory(prefix="cm-cli-handler-") as td:
            memory = Mock()
            memory.root = Path(td) / "memory"
            parser = Mock()
            args = SimpleNamespace(command="metrics", metrics_cmd="reviewer-quick-value-v2", strict=False, write=None)
            with patch("builtins.print"):
                handled = handle_metrics_command(
                    args=args,
                    memory=memory,
                    metrics_parser=parser,
                    canonical_health_report=lambda root, write_path=None: {"ok": True},
                )
            self.assertTrue(handled)


if __name__ == "__main__":
    unittest.main()
