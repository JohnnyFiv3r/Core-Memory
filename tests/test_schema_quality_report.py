import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore


class TestSchemaQualityReport(unittest.TestCase):
    def test_schema_quality_report_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(
                type="lesson",
                title="Redis incident",
                summary=["timeouts under load"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = Path(td) / "schema-report.md"
            rep = s.schema_quality_report(write_path=str(out))
            self.assertTrue(rep.get("ok"))
            self.assertTrue(out.exists())
            self.assertIn("total_beads", rep)


if __name__ == "__main__":
    unittest.main()
