#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestCompressionRatio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-cr-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finalize_computes_ratio_from_counters(self):
        self.store.start_task_run("r-cr", "task-cr")
        self.store.track_turn_processed(10)
        self.store.track_bead_created(2)

        rec = self.store.finalize_task_run(result="success")
        self.assertEqual(rec["turns_processed"], 10)
        self.assertEqual(rec["beads_created"], 2)
        self.assertAlmostEqual(rec["compression_ratio"], 5.0)


if __name__ == "__main__":
    unittest.main()
