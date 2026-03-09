import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from core_memory.openclaw_integration import finalize_and_process_turn, process_pending_memory_events
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.store import MemoryStore
from core_memory.trigger_orchestrator import run_flush_pipeline


class TestV2P2EnforcementMatrix(unittest.TestCase):
    def test_per_turn_canonical_path_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: keep canonical trigger path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out1.get("ok"))
            self.assertEqual("canonical_in_process", out1.get("authority_path"))
            self.assertEqual(1, out1.get("processed"))

            out2 = finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: keep canonical trigger path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            self.assertTrue(out2.get("ok"))
            self.assertEqual(0, out2.get("processed"))

    def test_flush_pipeline_writes_checkpoints(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["y"], session_id="main", source_turn_ids=["t1"])

            out = run_flush_pipeline(
                root=td,
                session_id="main",
                promote=False,
                token_budget=400,
                max_beads=20,
                source="flush_hook",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))

            ck = Path(td) / ".beads" / "events" / "flush-checkpoints.jsonl"
            self.assertTrue(ck.exists())
            text = ck.read_text(encoding="utf-8")
            self.assertIn('"stage": "start"', text)
            self.assertIn('"stage": "committed"', text)

    def test_admin_flush_cli_uses_canonical_path(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(**__import__("os").environ)
            env["CORE_MEMORY_ROOT"] = td
            s = MemoryStore(td)
            s.add_bead(type="context", title="x", summary=["y"], session_id="main", source_turn_ids=["t1"])

            proc = subprocess.run(
                [
                    "python3",
                    "/home/node/.openclaw/workspace/consolidate.py",
                    "flush",
                    "--session",
                    "main",
                    "--token-budget",
                    "400",
                    "--max-beads",
                    "20",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            out = json.loads(proc.stdout.strip())
            self.assertTrue(out.get("ok"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))

    def test_no_dual_authority_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            finalize_and_process_turn(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember this",
                assistant_final="Decision: keep canonical trigger path",
                policy=SidecarPolicy(create_threshold=0.6),
            )
            # Legacy poller should not reprocess already-done turn
            out = process_pending_memory_events(td, max_events=10)
            self.assertEqual("legacy_sidecar_compat", out.get("authority_path"))
            self.assertEqual(0, out.get("processed"))


if __name__ == "__main__":
    unittest.main()
