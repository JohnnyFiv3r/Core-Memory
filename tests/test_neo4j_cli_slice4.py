from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.store import MemoryStore


class TestNeo4jCliSlice4(unittest.TestCase):
    def _run_cli(self, root: str, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "core_memory.cli", "--root", root, *args]
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        cwd = Path(__file__).resolve().parents[1]
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=merged_env)

    def test_neo4j_status_default_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run_cli(td, ["graph", "neo4j-status"])
            self.assertEqual(0, out.returncode, out.stderr)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("disabled", payload.get("status"))

    def test_neo4j_status_strict_fails_when_enabled_but_unconfigured(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run_cli(
                td,
                ["graph", "neo4j-status", "--strict"],
                env={"CORE_MEMORY_NEO4J_ENABLED": "1"},
            )
            self.assertEqual(2, out.returncode)
            payload = json.loads(out.stdout)
            self.assertFalse(payload.get("ok"))
            err = payload.get("error") or {}
            self.assertEqual("neo4j_config_error", err.get("code"))

    def test_neo4j_sync_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="decision", title="d", summary=["a"], session_id="s1", source_turn_ids=["t1"])
            b2 = s.add_bead(type="outcome", title="o", summary=["b"], session_id="s1", source_turn_ids=["t2"])
            s.link(source_id=b1, target_id=b2, relationship="supports", explanation="why")

            out = self._run_cli(td, ["graph", "neo4j-sync", "--session-id", "s1", "--dry-run"])
            self.assertEqual(0, out.returncode, out.stderr)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("dry_run", payload.get("mode"))
            self.assertGreaterEqual(int(payload.get("nodes_planned") or 0), 2)
            self.assertGreaterEqual(int(payload.get("edges_planned") or 0), 1)

    def test_neo4j_sync_non_dry_run_disabled_returns_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            MemoryStore(td)
            out = self._run_cli(td, ["graph", "neo4j-sync", "--session-id", "s1"])
            self.assertEqual(2, out.returncode)
            payload = json.loads(out.stdout)
            self.assertFalse(payload.get("ok"))
            errs = payload.get("errors") or []
            self.assertTrue(errs)
            self.assertEqual("neo4j_disabled", (errs[0] or {}).get("code"))


if __name__ == "__main__":
    unittest.main()
