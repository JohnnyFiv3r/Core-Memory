from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestReviewerQuickValueEvalSlice46D(unittest.TestCase):
    def test_quick_value_eval_reports_behavior_change(self):
        cwd = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "eval/reviewer_quick_value_eval.py"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(cwd)},
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload.get("behavior_changed"))
        self.assertEqual("canary", payload.get("after_choice"))


if __name__ == "__main__":
    unittest.main()
