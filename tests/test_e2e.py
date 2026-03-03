#!/usr/bin/env python3
"""Core-memory end-to-end tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


class TestCoreMemoryE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-e2e-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        cmd = [sys.executable, "-m", "core_memory.cli", "--root", self.tmp, *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_add_query_compact_uncompact(self):
        add = self.run_cli("add", "--type", "decision", "--title", "E2E", "--because", "coverage", "--session-id", "s1")
        self.assertEqual(add.returncode, 0, add.stderr)
        bead_id = add.stdout.strip().split(":", 1)[1].strip()

        q = self.run_cli("query", "--type", "decision", "--limit", "5")
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertIn("[decision]", q.stdout)

        # inject detail directly then compact/uncompact round-trip
        idx_path = os.path.join(self.tmp, ".beads", "index.json")
        idx = json.loads(open(idx_path).read())
        idx["beads"][bead_id]["detail"] = "long detail"
        open(idx_path, "w").write(json.dumps(idx, indent=2))

        c = self.run_cli("compact", "--session", "s1", "--promote")
        self.assertEqual(c.returncode, 0, c.stderr)
        self.assertTrue(json.loads(c.stdout).get("ok"))

        u = self.run_cli("uncompact", "--id", bead_id)
        self.assertEqual(u.returncode, 0, u.stderr)
        self.assertTrue(json.loads(u.stdout).get("ok"))


if __name__ == "__main__":
    unittest.main()
