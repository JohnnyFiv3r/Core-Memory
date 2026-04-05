from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "core_memory.cli", *args]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


class TestCliSlice5Taxonomy(unittest.TestCase):
    def test_root_help_shows_canonical_memory_command(self):
        cwd = Path(__file__).resolve().parents[1]
        out = _run_cli(["--help"], cwd)
        self.assertEqual(0, out.returncode)
        self.assertIn("memory", out.stdout)

    def test_dev_help_hides_duplicated_memory_tree(self):
        cwd = Path(__file__).resolve().parents[1]
        out = _run_cli(["dev", "--help"], cwd)
        self.assertEqual(0, out.returncode)
        self.assertNotIn("memory", out.stdout)

    def test_memory_command_executes_search(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-cli-s5-") as td:
            root = Path(td) / "memory"
            init_out = _run_cli(["--root", str(root), "setup", "init"], cwd)
            self.assertEqual(0, init_out.returncode)

            add_out = _run_cli(
                [
                    "--root",
                    str(root),
                    "store",
                    "add",
                    "--type",
                    "decision",
                    "--title",
                    "CLI taxonomy",
                    "--summary",
                    "canonical",
                    "memory",
                    "--session-id",
                    "s1",
                    "--source-turn-ids",
                    "t1",
                ],
                cwd,
            )
            self.assertEqual(0, add_out.returncode)

            mem_out = _run_cli(["--root", str(root), "memory", "search", "--query", "taxonomy", "--k", "3"], cwd)
            self.assertEqual(0, mem_out.returncode)
            self.assertIn('"ok"', mem_out.stdout)

            # Hidden compatibility alias is still accepted for legacy automation.
            dev_out = _run_cli(["--root", str(root), "dev", "memory", "search", "--query", "taxonomy", "--k", "3"], cwd)
            self.assertEqual(0, dev_out.returncode)
            self.assertIn('"ok"', dev_out.stdout)


if __name__ == "__main__":
    unittest.main()
