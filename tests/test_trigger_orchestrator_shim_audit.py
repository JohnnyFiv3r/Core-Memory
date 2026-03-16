import json
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.trigger_pipeline import run_turn_finalize_pipeline


class TestTriggerOrchestratorShimAudit(unittest.TestCase):
    def test_shim_usage_is_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            out = run_turn_finalize_pipeline(
                root=td,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="tr1",
                user_query="remember",
                assistant_final="Decision: canonical path",
            )
            self.assertTrue(out.get("ok"))

            p = Path(td) / ".beads" / "events" / "legacy-shim-usage.jsonl"
            self.assertTrue(p.exists())
            rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(r.get("shim_call") == "run_turn_finalize_pipeline" for r in rows))


if __name__ == "__main__":
    unittest.main()
