from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestSemanticCli(unittest.TestCase):
    def test_semantic_status_is_json_and_read_only(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-semantic-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "semantic", "status"], cwd)
            self.assertEqual(0, out.returncode, out.stderr)
            data = json.loads(out.stdout)
            self.assertTrue(data["ok"])
            self.assertFalse(data["dirty"])
            self.assertEqual(0, data["queue_epoch"])
            self.assertEqual("delta", data["mode"])
            self.assertEqual({"turn_id": None, "flush_tx_id": None}, data["last_checkpoint"])
            self.assertFalse(root.exists(), "semantic status must not create store files")

    def test_semantic_rebuild_enqueues_mode_without_wait(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-semantic-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "semantic", "rebuild", "--mode", "reconcile"], cwd)
            self.assertEqual(0, out.returncode, out.stderr)
            data = json.loads(out.stdout)
            self.assertTrue(data["ok"])
            self.assertFalse(data["wait"])
            self.assertEqual("reconcile", data["mode"])
            self.assertEqual("reconcile", data["queue"]["mode"])
            self.assertTrue((root / ".beads" / "semantic" / "rebuild-queue.json").exists())

    def test_semantic_tail_summarizes_when_no_event_log(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-semantic-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "semantic", "tail", "-n", "3"], cwd)
            self.assertEqual(0, out.returncode, out.stderr)
            data = json.loads(out.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual([], data["entries"])
            self.assertIn("summary", data)
            self.assertFalse(root.exists(), "semantic tail without a log must not create store files")

    def test_semantic_doctor_outputs_json(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-semantic-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "semantic", "doctor"], cwd)
            self.assertEqual(0, out.returncode, out.stderr)
            data = json.loads(out.stdout)
            self.assertIn("ok", data)


if __name__ == "__main__":
    unittest.main()
