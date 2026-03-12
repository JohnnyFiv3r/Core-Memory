import json
import tempfile
import unittest
from pathlib import Path

from core_memory.memory_engine import process_turn_finalized, process_flush


class TestFlushReportArtifact(unittest.TestCase):
    def test_flush_writes_committed_and_skipped_reports(self):
        with tempfile.TemporaryDirectory() as td:
            process_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                user_query="remember this",
                assistant_final="decision + evidence",
            )
            first = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(first.get("ok"))

            second = process_flush(root=td, session_id="s1", promote=True, token_budget=1200, max_beads=12)
            self.assertTrue(second.get("ok"))
            self.assertTrue(second.get("skipped"))

            p = Path(td) / ".beads" / "events" / "flush-checkpoints.jsonl"
            self.assertTrue(p.exists())
            rows = []
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rows.append(json.loads(line))

            report_rows = [r for r in rows if str(r.get("schema") or "") == "openclaw.memory.flush_report.v1"]
            stages = [str(r.get("stage") or "") for r in report_rows]
            self.assertIn("committed", stages)
            self.assertIn("skipped", stages)


if __name__ == "__main__":
    unittest.main()
