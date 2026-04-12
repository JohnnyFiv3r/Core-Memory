from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.retrieval_feedback import record_retrieval_feedback


def _run_cli(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True, env=run_env)


class TestMetricsMyelinationCli(unittest.TestCase):
    def test_metrics_myelination_report_default_disabled(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-mye-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "metrics", "myelination-experiment", "--since", "30d"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertEqual("core_memory.myelination_experiment.v1", payload.get("schema"))
            self.assertFalse(bool(payload.get("enabled")))

    def test_metrics_myelination_report_enabled_with_signal(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-mye-cli-") as td:
            root = Path(td) / "memory"
            # Seed one successful retrieval feedback event with an explicit edge.
            record_retrieval_feedback(
                root,
                request={"raw_query": "q", "intent": "remember", "k": 5},
                response={
                    "ok": True,
                    "answer_outcome": "answer_current",
                    "results": [
                        {"bead_id": "bead-x", "score": 0.9, "source_surface": "session_bead"},
                        {"bead_id": "bead-y", "score": 0.8, "source_surface": "session_bead"},
                    ],
                    "chains": [{"edges": [{"src": "bead-x", "dst": "bead-y", "rel": "supports"}]}],
                },
                source="unit_test",
            )

            out = _run_cli(
                ["--root", str(root), "metrics", "myelination-experiment", "--since", "30d", "--strict"],
                cwd,
                env={
                    "CORE_MEMORY_MYELINATION_ENABLED": "1",
                    "CORE_MEMORY_MYELINATION_MIN_HITS": "1",
                },
            )
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(bool(payload.get("enabled")))
            self.assertTrue(list(payload.get("top_strengthened") or []) or list(payload.get("top_weakened") or []))


if __name__ == "__main__":
    unittest.main()
