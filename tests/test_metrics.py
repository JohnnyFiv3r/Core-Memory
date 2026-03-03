#!/usr/bin/env python3

import json
import shutil
import tempfile
import unittest

from core_memory.store import MemoryStore


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-metrics-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_metrics_log_and_report(self):
        self.store.append_metric({
            "run_id": "r1",
            "mode": "baseline",
            "task_id": "t1",
            "result": "fail",
            "steps": 10,
            "tool_calls": 4,
            "beads_created": 0,
            "beads_recalled": 0,
            "repeat_failure": True,
            "decision_conflicts": 2,
            "unjustified_flips": 1,
            "rationale_recall_score": 1,
            "compression_ratio": 0,
        })
        self.store.append_metric({
            "run_id": "r2",
            "mode": "core_memory",
            "task_id": "t1",
            "result": "success",
            "steps": 6,
            "tool_calls": 2,
            "beads_created": 2,
            "beads_recalled": 1,
            "repeat_failure": False,
            "decision_conflicts": 1,
            "unjustified_flips": 0,
            "rationale_recall_score": 2,
            "compression_ratio": 4,
        })

        report = self.store.metrics_report("7d")
        self.assertEqual(report["runs"], 2)
        self.assertEqual(report["median_steps"], 8)
        self.assertEqual(report["median_tool_calls"], 3)
        self.assertGreaterEqual(report["repeat_failure_rate"], 0.4)


if __name__ == "__main__":
    unittest.main()
