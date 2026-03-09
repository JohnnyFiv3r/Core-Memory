#!/usr/bin/env python3

import json
import os
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

    def test_finalize_then_process(self):
        fz = self.run_cli(
            "sidecar", "finalize",
            "--session-id", "s1",
            "--turn-id", "t1",
            "--transaction-id", "tx1",
            "--trace-id", "tr1",
            "--user-query", "remember this decision",
            "--assistant-final", "Decision: use stdlib",
        )
        self.assertEqual(fz.returncode, 0, fz.stderr)
        self.assertTrue(json.loads(fz.stdout).get("emitted"))

        env = dict(os.environ)
        env["CORE_MEMORY_ENABLE_LEGACY_POLLER"] = "1"
        pr = self.run_cli("sidecar", "process", "--max-events", "10", env=env)
        self.assertEqual(pr.returncode, 0, pr.stderr)
        self.assertGreaterEqual(json.loads(pr.stdout).get("processed", 0), 1)


if __name__ == "__main__":
    unittest.main()
