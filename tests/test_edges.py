#!/usr/bin/env python3
"""Core-memory edge/association tests."""

import os
import shutil
import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestCoreAssociations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-edges-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_link_basic(self):
        a = self.store.add_bead(type="decision", title="A", session_id="s", because=["chosen for test"])
        b = self.store.add_bead(type="outcome", title="B", session_id="s")
        assoc = self.store.link(b, a, "follows")
        self.assertTrue(assoc.startswith("assoc-"))

        idx = self.store._read_json(self.store.beads_dir / "index.json")
        self.assertTrue(any(x.get("source_bead") == b and x.get("target_bead") == a for x in idx["associations"]))

    def test_recall_updates_counts(self):
        bead = self.store.add_bead(type="lesson", title="L", session_id="s", because=["learned in test"])
        ok = self.store.recall(bead)
        self.assertTrue(ok)
        row = self.store._read_json(self.store.beads_dir / "index.json")["beads"][bead]
        self.assertEqual(row.get("recall_count"), 1)


if __name__ == "__main__":
    unittest.main()
