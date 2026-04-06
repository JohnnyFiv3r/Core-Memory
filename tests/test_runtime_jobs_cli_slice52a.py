from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestRuntimeJobsCliSlice52A(unittest.TestCase):
    def test_ops_jobs_status_prints_async_queue_report(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "ops", "jobs-status"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertIn("queues", payload)
            self.assertIn("semantic_rebuild", payload.get("queues") or {})
            self.assertIn("compaction", payload.get("queues") or {})

    def test_hidden_legacy_alias_still_works(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "async-jobs-status"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertIn("pending_total", payload)

    def test_ops_jobs_run_executes_bounded_drain_pass(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "ops", "jobs-run", "--max-compaction", "0"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertIn("semantic_run", payload)
            self.assertIn("compaction_run", payload)
            self.assertIn("status_after", payload)

    def test_hidden_legacy_run_alias_still_works(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "async-jobs-run", "--max-compaction", "0"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertIn("semantic_before", payload)


if __name__ == "__main__":
    unittest.main()
