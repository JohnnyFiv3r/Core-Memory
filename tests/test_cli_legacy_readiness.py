import json
import tempfile
import unittest
from pathlib import Path

from core_memory.cli import _legacy_readiness_report


class TestCliLegacyReadiness(unittest.TestCase):
    def test_report_counts_legacy_usage(self):
        with tempfile.TemporaryDirectory() as td:
            events = Path(td) / ".beads" / "events"
            events.mkdir(parents=True, exist_ok=True)

            (events / "legacy-shim-usage.jsonl").write_text(
                json.dumps({"shim_call": "run_turn_finalize_pipeline"}) + "\n",
                encoding="utf-8",
            )
            (events / "write-trigger-processed.jsonl").write_text(
                json.dumps({"status": "done"}) + "\n" + json.dumps({"status": "blocked"}) + "\n",
                encoding="utf-8",
            )

            out = _legacy_readiness_report(td)
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("ready_for_legacy_removal"))
            self.assertEqual(1, (out.get("summary") or {}).get("shim_usage_count"))
            self.assertEqual(1, (out.get("summary") or {}).get("legacy_dispatch_count"))
            self.assertEqual(1, (out.get("summary") or {}).get("legacy_dispatch_blocked_count"))


if __name__ == "__main__":
    unittest.main()
