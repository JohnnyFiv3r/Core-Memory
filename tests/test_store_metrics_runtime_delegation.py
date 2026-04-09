from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore


class TestStoreMetricsRuntimeDelegationSlice68A(unittest.TestCase):
    def test_start_task_run_delegates_to_reporting_runtime_module(self):
        with tempfile.TemporaryDirectory(prefix="cm-metrics-deleg-") as td:
            store = MemoryStore(td)
            expected = {"run_id": "r1", "task_id": "t1", "steps": 0}
            with patch("core_memory.reporting.store_metrics_runtime.start_task_run_for_store", return_value=expected) as stub:
                out = store.start_task_run("r1", "t1")

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual("r1", args[1])
            self.assertEqual("t1", args[2])

    def test_append_metric_delegates_to_reporting_runtime_module(self):
        with tempfile.TemporaryDirectory(prefix="cm-metrics-deleg-") as td:
            store = MemoryStore(td)
            expected = {"ok": True, "run_id": "r2"}
            with patch("core_memory.reporting.store_metrics_runtime.append_metric_for_store", return_value=expected) as stub:
                out = store.append_metric({"run_id": "r2"})

            self.assertEqual(expected, out)
            self.assertEqual(1, stub.call_count)
            args, kwargs = stub.call_args
            self.assertIs(args[0], store)
            self.assertEqual({"run_id": "r2"}, args[1])


if __name__ == "__main__":
    unittest.main()
