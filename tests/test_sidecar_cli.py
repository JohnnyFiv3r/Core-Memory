#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
import tempfile
import unittest


class TestSidecarCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-sidecar-cli-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, env=None):
        cmd = [sys.executable, "-m", "core_memory.cli", "--root", self.tmp, *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_turn_canonical_idempotent(self):
        out1 = self.run_cli(
            "sidecar", "turn",
            "--session-id", "s1",
            "--turn-id", "t1",
            "--transaction-id", "tx1",
            "--trace-id", "tr1",
            "--user-query", "remember this decision",
            "--assistant-final", "Decision: use stdlib",
        )
        self.assertEqual(out1.returncode, 0, out1.stderr)
        j1 = json.loads(out1.stdout)
        self.assertTrue(j1.get("ok"))
        self.assertEqual("canonical_in_process", j1.get("authority_path"))

        out2 = self.run_cli(
            "sidecar", "turn",
            "--session-id", "s1",
            "--turn-id", "t1",
            "--transaction-id", "tx1",
            "--trace-id", "tr1",
            "--user-query", "remember this decision",
            "--assistant-final", "Decision: use stdlib",
        )
        self.assertEqual(out2.returncode, 0, out2.stderr)
        j2 = json.loads(out2.stdout)
        self.assertEqual(0, j2.get("processed"))


if __name__ == "__main__":
    unittest.main()
