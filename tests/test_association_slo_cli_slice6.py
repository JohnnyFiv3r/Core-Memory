from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence import events


class TestAssociationSloCliSlice6(unittest.TestCase):
    def _run_cli(self, root: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-m", "core_memory.cli", "--root", root, *args]
        cwd = Path(__file__).resolve().parents[1]
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)

    def test_association_slo_check_non_strict(self):
        with tempfile.TemporaryDirectory() as td:
            events.append_metric(Path(td), {"task_id": "agent_turn_quality", "result": "success", "agent_source": "default_fallback", "agent_used_fallback": True, "non_temporal_semantic_count": 0})
            out = self._run_cli(td, ["graph", "association-slo-check", "--min-agent-authored-rate", "0.99"])
            self.assertEqual(0, out.returncode, out.stderr)
            payload = json.loads(out.stdout)
            self.assertIn("ok", payload)

    def test_association_slo_check_strict_exit(self):
        with tempfile.TemporaryDirectory() as td:
            events.append_metric(Path(td), {"task_id": "agent_turn_quality", "result": "success", "agent_source": "default_fallback", "agent_used_fallback": True, "non_temporal_semantic_count": 0})
            out = self._run_cli(td, ["graph", "association-slo-check", "--strict", "--min-agent-authored-rate", "0.99"])
            self.assertEqual(2, out.returncode)


if __name__ == "__main__":
    unittest.main()
