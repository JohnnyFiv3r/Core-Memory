from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "core_memory.cli", *args]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


class TestCliGroupedSurfaceSlice1(unittest.TestCase):
    def test_help_boots(self):
        cwd = Path(__file__).resolve().parents[1]
        out = _run_cli(["--help"], cwd)
        self.assertEqual(0, out.returncode)
        self.assertIn("setup", out.stdout)
        self.assertIn("store", out.stdout)
        self.assertIn("memory", out.stdout)
        self.assertIn("inspect", out.stdout)
        self.assertNotIn("myelinate", out.stdout)
        self.assertNotIn("retrieve-context", out.stdout)

    def test_bare_group_prints_group_help(self):
        cwd = Path(__file__).resolve().parents[1]
        cases = {
            "setup": "init",
            "memory": "search",
            "integrations": "openclaw",
        }
        for group, expected in cases.items():
            out = _run_cli([group], cwd)
            self.assertEqual(0, out.returncode)
            self.assertIn(expected, out.stdout)

    def test_group_help_boots(self):
        cwd = Path(__file__).resolve().parents[1]
        for group in ["setup", "store", "memory", "inspect", "integrations", "ops", "dev"]:
            out = _run_cli([group, "--help"], cwd)
            self.assertEqual(0, out.returncode)
            self.assertIn("usage:", out.stdout)

    def test_happy_path_per_group(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-cli-") as td:
            root = Path(td) / "memory"

            init_out = _run_cli(["--root", str(root), "setup", "init"], cwd)
            self.assertEqual(0, init_out.returncode)
            self.assertTrue((root / ".beads").exists())

            doctor_out = _run_cli(["--root", str(root), "setup", "doctor"], cwd)
            self.assertEqual(0, doctor_out.returncode)
            self.assertIn('"ok": true', doctor_out.stdout.lower())

            add_out = _run_cli(
                [
                    "--root",
                    str(root),
                    "store",
                    "add",
                    "--type",
                    "decision",
                    "--title",
                    "CLI test",
                    "--summary",
                    "grouped",
                    "surface",
                    "--session-id",
                    "s1",
                    "--source-turn-ids",
                    "t1",
                ],
                cwd,
            )
            self.assertEqual(0, add_out.returncode)

            doctor_after_add = _run_cli(["--root", str(root), "setup", "doctor"], cwd)
            self.assertEqual(0, doctor_after_add.returncode)
            self.assertIn('"ok": true', doctor_after_add.stdout.lower())

            recall_out = _run_cli(
                [
                    "--root",
                    str(root),
                    "memory",
                    "search",
                    "--query",
                    "cli test",
                    "--k",
                    "5",
                ],
                cwd,
            )
            self.assertEqual(0, recall_out.returncode)
            self.assertIn('"ok"', recall_out.stdout)
            self.assertIn("CLI test", recall_out.stdout)

            trace_out = _run_cli(["--root", str(root), "memory", "trace", "--query", "cli test", "--k", "3"], cwd)
            self.assertEqual(0, trace_out.returncode)
            self.assertIn('"anchors"', trace_out.stdout)

            # Recall remains compatibility-only alias for legacy automation.
            recall_alias_out = _run_cli(["--root", str(root), "recall", "search", "cli test", "--k", "3"], cwd)
            self.assertEqual(0, recall_alias_out.returncode)
            self.assertIn('"ok"', recall_alias_out.stdout)

            inspect_out = _run_cli(["--root", str(root), "inspect", "stats"], cwd)
            self.assertEqual(0, inspect_out.returncode)
            self.assertIn('"total_beads"', inspect_out.stdout)

            integrations_out = _run_cli(["--root", str(root), "integrations", "migrate", "rebuild-turn-indexes"], cwd)
            self.assertEqual(0, integrations_out.returncode)
            self.assertIn('"ok"', integrations_out.stdout)

            ops_out = _run_cli(["--root", str(root), "ops", "rebuild"], cwd)
            self.assertEqual(0, ops_out.returncode)
            self.assertIn("Rebuilt index", ops_out.stdout)

            dev_out = _run_cli(["--root", str(root), "dev", "memory", "search", "--query", "cli test", "--k", "3"], cwd)
            self.assertEqual(0, dev_out.returncode)
            self.assertIn('"ok"', dev_out.stdout)


if __name__ == "__main__":
    unittest.main()
