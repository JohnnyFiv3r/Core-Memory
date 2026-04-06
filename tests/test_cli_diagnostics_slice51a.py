from __future__ import annotations

import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.cli_diagnostics import doctor_report, simple_recall_fallback


class TestCliDiagnosticsSlice51A(unittest.TestCase):
    def test_simple_recall_fallback_finds_matching_beads(self):
        with tempfile.TemporaryDirectory() as td:
            memory = MemoryStore(td)
            memory.add_bead(
                type="decision",
                title="Redis pool fix",
                summary=["increase pool size"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            out = simple_recall_fallback(memory, "redis pool", limit=5)
            self.assertTrue(out.get("ok"))
            self.assertGreaterEqual(len(out.get("results") or []), 1)

    def test_doctor_report_runs_on_fresh_store(self):
        with tempfile.TemporaryDirectory() as td:
            _ = MemoryStore(td)
            out = doctor_report(td)
            self.assertIn("ok", out)
            self.assertIn("checks", out)
            self.assertGreaterEqual(len(out.get("checks") or []), 1)


if __name__ == "__main__":
    unittest.main()
