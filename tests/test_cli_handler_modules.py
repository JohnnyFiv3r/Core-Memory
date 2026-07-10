from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
from pathlib import Path

from core_memory.runtime.dreamer.candidates import enqueue_dreamer_candidates
from core_memory.graph.core import build_graph
from core_memory.persistence.store import MemoryStore

from core_memory.cli.handlers.store import handle_store_commands
from core_memory.cli.handlers.graph import handle_graph_command
from core_memory.cli.handlers.metrics import handle_metrics_command


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

    def test_store_handler_routes_dream_to_runtime_analysis(self):
        memory = Mock()
        args = SimpleNamespace(command="dream", novel_only=True, seen_window_runs=2, max_exposure=1)
        expected = [{"relationship": "supports"}]
        with patch("core_memory.cli.handlers.store.run_analysis", return_value=expected) as stub, patch("builtins.print"):
            handled = handle_store_commands(args=args, memory=memory, doctor_report=lambda _r: {"ok": True})
        self.assertTrue(handled)
        stub.assert_called_once_with(store=memory, novel_only=True, seen_window_runs=2, max_exposure=1)
        self.assertFalse(memory.dream.called)

    def test_graph_handler_unknown_prints_help(self):
        memory = Mock()
        parser = Mock()
        args = SimpleNamespace(command="graph", graph_cmd="unknown")
        handled = handle_graph_command(args=args, memory=memory, graph_parser=parser)
        self.assertTrue(handled)
        parser.print_help.assert_called_once()

    def test_graph_handler_legacy_causal_apply_is_candidate_only(self):
        with tempfile.TemporaryDirectory(prefix="cm-cli-handler-") as td:
            root = Path(td)
            store = MemoryStore(root)
            store.add_bead(type="decision", title="Candidate promotion policy", summary=["candidate only promotion"], session_id="main", source_turn_ids=["t1"])
            store.add_bead(type="evidence", title="Promotion inflation evidence", summary=["candidate promotion issue"], session_id="main", source_turn_ids=["t1"])
            memory = SimpleNamespace(root=root)
            args = SimpleNamespace(
                command="graph",
                graph_cmd="backfill-causal-links",
                apply=True,
                bead_id=[],
                bead_ids_file=None,
                max_per_target=3,
                min_overlap=1,
                no_require_shared_turn=False,
            )

            with self.assertWarns(DeprecationWarning), patch("builtins.print") as printed:
                handled = handle_graph_command(args=args, memory=memory, graph_parser=Mock())

            self.assertTrue(handled)
            payload = printed.call_args.args[0]
            self.assertIn('"candidate_only": true', payload)
            self.assertEqual(0, int(build_graph(root, write_snapshot=False).get("structural_edges", 0)))

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
