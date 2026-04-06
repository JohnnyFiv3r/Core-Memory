from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestLongitudinalLearningEvalSlice46C(unittest.TestCase):
    def _run(self, rel_path: str) -> subprocess.CompletedProcess[str]:
        cwd = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, rel_path],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(cwd)},
        )

    def test_eval_reports_learning_improvement(self):
        proc = self._run("eval/longitudinal_learning_eval.py")
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        payload = json.loads(proc.stdout)

        no_mem = payload["results"]["no_memory"]
        summary = payload["results"]["summary_only"]
        core = payload["results"]["core_memory"]

        self.assertLess(core["repeated_mistake_rate"], no_mem["repeated_mistake_rate"])
        self.assertLess(core["repeated_mistake_rate"], summary["repeated_mistake_rate"])
        self.assertGreater(core["lesson_reuse_across_sessions"], summary["lesson_reuse_across_sessions"])

    def test_behavior_proof_demo_shows_changed_choice(self):
        proc = self._run("examples/proof_policy_reuse.py")
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("behavior_changed"))
        self.assertEqual("canary", payload.get("after_choice"))


if __name__ == "__main__":
    unittest.main()
