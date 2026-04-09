from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "core_memory.cli", *args], cwd=str(cwd), capture_output=True, text=True)


class TestRuntimeJobsCliSlice52A(unittest.TestCase):
    def test_ops_jobs_enqueue_semantic_sets_semantic_pending(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            enq = _run_cli(["--root", str(root), "ops", "jobs-enqueue", "--kind", "semantic-rebuild"], cwd)
            self.assertEqual(0, enq.returncode)
            enq_payload = json.loads(enq.stdout)
            self.assertTrue(enq_payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", enq_payload.get("schema_version"))

            status = _run_cli(["--root", str(root), "ops", "jobs-status"], cwd)
            self.assertEqual(0, status.returncode)
            payload = json.loads(status.stdout)
            sem = ((payload.get("queues") or {}).get("semantic_rebuild") or {})
            self.assertTrue(sem.get("queued"))
            self.assertEqual(1, sem.get("pending"))

    def test_ops_jobs_status_prints_async_queue_report(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "ops", "jobs-status"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", payload.get("schema_version"))
            self.assertIn("queues", payload)
            self.assertIn("semantic_rebuild", payload.get("queues") or {})
            self.assertIn("compaction", payload.get("queues") or {})
            self.assertIn("side_effects", payload.get("queues") or {})

    def test_hidden_legacy_alias_still_works(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "async-jobs-status"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", payload.get("schema_version"))
            self.assertIn("pending_total", payload)

    def test_ops_jobs_run_executes_bounded_drain_pass(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "ops", "jobs-run", "--max-compaction", "0"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", payload.get("schema_version"))
            self.assertIn("semantic_run", payload)
            self.assertIn("compaction_run", payload)
            self.assertIn("side_effect_run", payload)
            self.assertIn("status_after", payload)

    def test_hidden_legacy_run_alias_still_works(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(["--root", str(root), "async-jobs-run", "--max-compaction", "0"], cwd)
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", payload.get("schema_version"))
            self.assertIn("semantic_before", payload)

    def test_hidden_legacy_enqueue_alias_still_works_for_compaction(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(
                [
                    "--root",
                    str(root),
                    "async-jobs-enqueue",
                    "--kind",
                    "compaction",
                    "--session-id",
                    "main",
                    "--run-id",
                    "r-1",
                ],
                cwd,
            )
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("core_memory.async_jobs.v1", payload.get("schema_version"))

            status = _run_cli(["--root", str(root), "ops", "jobs-status"], cwd)
            self.assertEqual(0, status.returncode)
            status_payload = json.loads(status.stdout)
            comp = ((status_payload.get("queues") or {}).get("compaction") or {})
            self.assertGreaterEqual(int(comp.get("queue_depth") or 0), 1)

    def test_jobs_enqueue_invalid_event_file_returns_structured_error(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"
            bad = Path(td) / "bad.json"
            bad.write_text("{not-json", encoding="utf-8")

            out = _run_cli(
                [
                    "--root",
                    str(root),
                    "ops",
                    "jobs-enqueue",
                    "--kind",
                    "compaction",
                    "--event-file",
                    str(bad),
                ],
                cwd,
            )
            self.assertEqual(2, out.returncode)
            payload = json.loads(out.stdout)
            self.assertFalse(payload.get("ok"))
            err = payload.get("error") or {}
            self.assertEqual("event_file_invalid_json", err.get("code"))

    def test_jobs_enqueue_dreamer_side_effect_kind(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-ops-jobs-") as td:
            root = Path(td) / "memory"

            out = _run_cli(
                [
                    "--root",
                    str(root),
                    "ops",
                    "jobs-enqueue",
                    "--kind",
                    "dreamer-run",
                    "--session-id",
                    "main",
                ],
                cwd,
            )
            self.assertEqual(0, out.returncode)
            payload = json.loads(out.stdout)
            self.assertTrue(payload.get("ok"))
            self.assertEqual("dreamer-run", payload.get("kind"))

            status = _run_cli(["--root", str(root), "ops", "jobs-status"], cwd)
            self.assertEqual(0, status.returncode)
            st = json.loads(status.stdout)
            side = ((st.get("queues") or {}).get("side_effects") or {})
            self.assertGreaterEqual(int(side.get("queue_depth") or 0), 1)


if __name__ == "__main__":
    unittest.main()
