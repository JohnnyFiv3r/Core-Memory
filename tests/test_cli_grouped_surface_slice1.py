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
        self.assertIn("recall", out.stdout)
        self.assertIn("inspect", out.stdout)
        self.assertNotIn("myelinate", out.stdout)
        self.assertNotIn("retrieve-context", out.stdout)

    def test_bare_group_prints_group_help(self):
        cwd = Path(__file__).resolve().parents[1]
        cases = {
            "setup": "init",
            "recall": "search",
            "integrations": "openclaw",
        }
        for group, expected in cases.items():
            out = _run_cli([group], cwd)
            self.assertEqual(0, out.returncode)
            self.assertIn(expected, out.stdout)

    def test_group_help_boots(self):
        cwd = Path(__file__).resolve().parents[1]
        for group in ["setup", "store", "recall", "inspect", "integrations", "ops", "dev"]:
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

            recall_out = _run_cli(
                [
                    "--root",
                    str(root),
                    "recall",
                    "search",
                    "--typed",
                    '{"intent":"remember","query_text":"cli test","k":5}',
                ],
                cwd,
            )
            self.assertEqual(0, recall_out.returncode)
            self.assertIn('"ok"', recall_out.stdout)

            inspect_out = _run_cli(["--root", str(root), "inspect", "stats"], cwd)
            self.assertEqual(0, inspect_out.returncode)
            self.assertIn('"total_beads"', inspect_out.stdout)

            integrations_out = _run_cli(["--root", str(root), "integrations", "migrate", "rebuild-turn-indexes"], cwd)
            self.assertEqual(0, integrations_out.returncode)
            self.assertIn('"ok"', integrations_out.stdout)

            ops_out = _run_cli(["--root", str(root), "ops", "rebuild"], cwd)
            self.assertEqual(0, ops_out.returncode)
            self.assertIn("Rebuilt index", ops_out.stdout)

            dev_out = _run_cli(["--root", str(root), "dev", "memory", "form"], cwd)
            self.assertEqual(0, dev_out.returncode)
            self.assertIn('"schema_version"', dev_out.stdout)


if __name__ == "__main__":
    unittest.main()
