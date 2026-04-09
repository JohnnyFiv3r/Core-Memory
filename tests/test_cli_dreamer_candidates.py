from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates, list_dreamer_candidates


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestCliDreamerCandidatesSlice62A(unittest.TestCase):
    def test_ops_dreamer_candidates_and_decide(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-dc-cli-") as td:
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
            pending = list_dreamer_candidates(root=root, status="pending", limit=10)
            cid = str(((pending.get("results") or [{}])[0].get("id") or ""))
            self.assertTrue(cid)

            out_list = _run_cli(["--root", str(root), "ops", "dreamer-candidates", "--status", "pending"], cwd)
            self.assertEqual(0, out_list.returncode)
            listed = json.loads(out_list.stdout)
            self.assertTrue(listed.get("ok"))
            self.assertGreaterEqual(int(listed.get("count") or 0), 1)

            out_decide = _run_cli(
                [
                    "--root",
                    str(root),
                    "ops",
                    "dreamer-decide",
                    "--id",
                    cid,
                    "--decision",
                    "reject",
                    "--reviewer",
                    "qa",
                ],
                cwd,
            )
            self.assertEqual(0, out_decide.returncode)
            dec = json.loads(out_decide.stdout)
            self.assertTrue(dec.get("ok"))
            self.assertEqual("rejected", dec.get("status"))


if __name__ == "__main__":
    unittest.main()
