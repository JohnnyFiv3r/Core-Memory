#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestMetricsAggregationState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-mstate-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_step_tool_aggregation_into_log(self):
        self.store.start_task_run("r-1", "task-A", mode="baseline", phase="baseline")
        self.store.track_step(2)
        self.store.track_step(1)
        self.store.track_tool_call(3)

        rec = self.store.append_metric({
            "result": "success",
            "beads_created": 0,
            "beads_recalled": 0,
        })

        self.assertEqual(rec["run_id"], "r-1")
        self.assertEqual(rec["task_id"], "task-A")
        self.assertEqual(rec["steps"], 3)
        self.assertEqual(rec["tool_calls"], 3)
        self.assertEqual(rec["mode"], "baseline")


if __name__ == "__main__":
    unittest.main()
