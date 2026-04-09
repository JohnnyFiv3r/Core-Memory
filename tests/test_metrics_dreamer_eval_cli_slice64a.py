from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestMetricsDreamerEvalCliSlice64A(unittest.TestCase):
    def test_metrics_dreamer_eval_outputs_report(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-dream-eval-cli-") as td:
            root = Path(td) / "memory"
            enqueue_dreamer_candidates(
                root=root,
                associations=[
                    {
                        "source": "b1",
                        "target": "b2",
                        "relationship": "transferable_lesson",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.7,
                    }
                ],
                run_metadata={"run_id": "r1", "mode": "suggest", "session_id": "s1"},
            )
            pending = list_dreamer_candidates(root=root, status="pending", limit=10).get("results") or []
            cid = str((pending[0] or {}).get("id") or "")
            self.assertTrue(cid)
            dec = decide_dreamer_candidate(root=root, candidate_id=cid, decision="accept", reviewer="qa")
            self.assertTrue(dec.get("ok"))

            out = _run_cli(["--root", str(root), "metrics", "dreamer-eval", "--since", "30d"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertEqual("core_memory.dreamer_eval.v1", payload.get("schema"))
            self.assertIn("metrics", payload)

    def test_metrics_dreamer_eval_strict_can_fail_with_no_signal(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-dream-eval-cli-") as td:
            root = Path(td) / "memory"
            out = _run_cli(["--root", str(root), "metrics", "dreamer-eval", "--strict"], cwd)
            self.assertEqual(2, out.returncode)
            payload = json.loads(out.stdout)
            self.assertEqual("core_memory.dreamer_eval.v1", payload.get("schema"))


if __name__ == "__main__":
    unittest.main()
