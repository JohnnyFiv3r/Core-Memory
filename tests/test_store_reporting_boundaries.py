import unittest


class TestStoreReportingBoundariesSlice46A(unittest.TestCase):
    def test_reporting_package_exports_current_helpers(self):
        from core_memory import reporting
        from core_memory.persistence import store_reporting

        self.assertIs(reporting.metrics_report_for_store, store_reporting.metrics_report_for_store)
        self.assertIs(reporting.autonomy_report_for_store, store_reporting.autonomy_report_for_store)
        self.assertIs(reporting.schema_quality_report_for_store, store_reporting.schema_quality_report_for_store)


if __name__ == "__main__":
    unittest.main()
