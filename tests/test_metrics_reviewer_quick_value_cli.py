from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestMetricsReviewerQuickValueCliSlice66A(unittest.TestCase):
    def test_metrics_reviewer_quick_value_v2_outputs_report(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-rqv2-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "metrics", "reviewer-quick-value-v2"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertEqual("core_memory.reviewer_quick_value_v2.v1", payload.get("schema"))
            self.assertIn("overall", payload)

    def test_metrics_reviewer_quick_value_v2_strict_passes(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-rqv2-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "metrics", "reviewer-quick-value-v2", "--strict"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(bool(((payload.get("overall") or {}).get("quick_value_passed"))))


if __name__ == "__main__":
    unittest.main()
