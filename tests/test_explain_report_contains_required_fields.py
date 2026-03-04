import json
import tempfile
import unittest
from pathlib import Path

from core_memory.tools.memory_reason import memory_reason
from core_memory.store import MemoryStore


class TestExplainReportFields(unittest.TestCase):
    def test_explain_report_has_required_fields(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Candidate gate", summary=["candidate only"], session_id="main", source_turn_ids=["t1"])
            out = memory_reason("why candidate only", root=td, explain=True, debug=True)
            self.assertTrue(out.get("ok"))
            ex = out.get("explain") or {}
            self.assertIn("report", ex)
            p = Path(ex.get("report"))
            self.assertTrue(p.exists())
            data = json.loads(p.read_text(encoding="utf-8"))
            for key in ["query", "normalized_query", "intent", "confidence", "retrieval_debug", "final_bead_ids"]:
                self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
