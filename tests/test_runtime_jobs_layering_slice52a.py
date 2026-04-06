from __future__ import annotations

import unittest
from pathlib import Path


class TestRuntimeJobsLayeringSlice52A(unittest.TestCase):
    def test_runtime_jobs_not_coupled_to_openclaw_integration_queue_module(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "core_memory" / "runtime" / "jobs.py").read_text(encoding="utf-8")
        self.assertNotIn("integrations.openclaw_compaction_queue", text)
        self.assertIn("runtime.compaction_queue", text)

    def test_openclaw_queue_wrapper_delegates_to_runtime_queue(self):
        repo = Path(__file__).resolve().parents[1]
        text = (repo / "core_memory" / "integrations" / "openclaw_compaction_queue.py").read_text(encoding="utf-8")
        self.assertIn("core_memory.runtime.compaction_queue", text)


if __name__ == "__main__":
    unittest.main()
