#!/usr/bin/env python3
"""Rebuild integrity tests for core_memory."""

import shutil
import tempfile
import unittest

from core_memory.store import MemoryStore


class TestRebuildIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-rebuild-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rebuild_preserves_bead_counts(self):
        self.store.add_bead(type="decision", title="D1", because=["reason"], session_id="s1")
        self.store.add_bead(type="lesson", title="L1", because=["learned"], session_id="s1")

        before = self.store.stats()
        self.store.rebuild_index()
        after = self.store.stats()

        self.assertEqual(before["total_beads"], after["total_beads"])
        self.assertEqual(before["by_type"], after["by_type"])

    def test_rebuild_preserves_associations(self):
        a = self.store.add_bead(type="decision", title="A", because=["r"], session_id="s1")
        b = self.store.add_bead(type="outcome", title="B", summary=["done"], session_id="s1")
        self.store.link(source_id=b, target_id=a, relationship="led_to", explanation="test")

        pre = self.store._read_json(self.store.beads_dir / "index.json")
        pre_assoc_ids = sorted([x.get("id") for x in pre.get("associations", []) if x.get("id")])
        self.assertGreaterEqual(len(pre_assoc_ids), 1)

        self.store.rebuild_index()

        post = self.store._read_json(self.store.beads_dir / "index.json")
        post_assoc_ids = sorted([x.get("id") for x in post.get("associations", []) if x.get("id")])

        self.assertEqual(pre_assoc_ids, post_assoc_ids)


if __name__ == "__main__":
    unittest.main()
