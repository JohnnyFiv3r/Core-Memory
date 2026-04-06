import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreReportingBoundariesSlice46A(unittest.TestCase):
    def test_metrics_report_delegates_to_reporting_module(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with patch("core_memory.reporting.store_reporting.metrics_report_for_store", return_value={"runs": 0}) as spy:
                out = s.metrics_report("7d")
            self.assertEqual({"runs": 0}, out)
            self.assertEqual(1, spy.call_count)

    def test_autonomy_report_delegates_to_reporting_module(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with patch("core_memory.reporting.store_reporting.autonomy_report_for_store", return_value={"runs": 0}) as spy:
                out = s.autonomy_report("7d")
            self.assertEqual({"runs": 0}, out)
            self.assertEqual(1, spy.call_count)

    def test_schema_quality_report_delegates_to_reporting_module(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with patch("core_memory.reporting.store_reporting.schema_quality_report_for_store", return_value={"ok": True}) as spy:
                out = s.schema_quality_report(write_path=None)
            self.assertEqual({"ok": True}, out)
            self.assertEqual(1, spy.call_count)


if __name__ == "__main__":
    unittest.main()
