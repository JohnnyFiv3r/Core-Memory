import tempfile
import unittest
from pathlib import Path

from core_memory.memory_engine import process_turn_finalized, process_flush
from core_memory.sidecar_worker import SidecarPolicy
from core_memory.tools.memory import execute


class TestE2ELifecycleQuery(unittest.TestCase):
    def test_turns_flush_new_session_query(self):
        with tempfile.TemporaryDirectory() as td:
            policy = SidecarPolicy(create_threshold=0.6)

            # Session A turns (write + enrichment)
            t1 = process_turn_finalized(
                root=td,
                session_id="session-a",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember we chose candidate-first promotion",
                assistant_final="Decision: enforce candidate-only promotion policy to prevent promotion inflation.",
                policy=policy,
            )
            self.assertTrue(t1.get("ok"))
            self.assertEqual("canonical_in_process", t1.get("authority_path"))

            t2 = process_turn_finalized(
                root=td,
                session_id="session-a",
                turn_id="t2",
                transaction_id="tx2",
                trace_id="tr2",
                user_query="also remember retrieval should prefer archive graph for durable queries",
                assistant_final="Decision: durable queries use archive-graph-oriented retrieval via memory.execute.",
                policy=policy,
            )
            self.assertTrue(t2.get("ok"))

            # Flush boundary for session A
            fl = process_flush(
                root=td,
                session_id="session-a",
                promote=False,
                token_budget=1200,
                max_beads=80,
                source="flush_hook",
            )
            self.assertTrue(fl.get("ok"))
            self.assertEqual("canonical_in_process", fl.get("authority_path"))

            # Rolling artifacts should exist (path returned by canonical pipeline)
            written = str((fl.get("result") or {}).get("written") or "")
            self.assertTrue(written)
            self.assertTrue(Path(written).exists())
            self.assertTrue(Path(written).with_name("promoted-context.meta.json").exists())

            # New session retrieval query should be answerable from durable memory
            out = execute(
                {
                    "raw_query": "remember candidate-only promotion policy",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 8,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("answer", out.get("next_action"))
            self.assertGreater(len(out.get("results") or []), 0)


if __name__ == "__main__":
    unittest.main()
