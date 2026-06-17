#!/usr/bin/env python3
"""Rebuild integrity tests for core_memory."""

import shutil
import tempfile
import unittest

from core_memory.persistence import events
from core_memory.persistence.store import MemoryStore


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
        self.store.link(source_id=b, target_id=a, relationship="leads_to", explanation="test")

        pre = self.store._read_json(self.store.beads_dir / "index.json")
        pre_assoc_ids = sorted([x.get("id") for x in pre.get("associations", []) if x.get("id")])
        self.assertGreaterEqual(len(pre_assoc_ids), 1)

        self.store.rebuild_index()

        post = self.store._read_json(self.store.beads_dir / "index.json")
        post_assoc_ids = sorted([x.get("id") for x in post.get("associations", []) if x.get("id")])

        self.assertEqual(pre_assoc_ids, post_assoc_ids)

    def test_rebuild_canonicalizes_legacy_caused_by_direction(self):
        effect = self.store.add_bead(type="outcome", title="Effect", summary=["effect"], session_id="s1")
        cause = self.store.add_bead(type="state_assertion", title="Cause", summary=["cause"], session_id="s1")
        events.event_association_created(
            self.store.root,
            {
                "id": "legacy-caused-by",
                "source_bead": effect,
                "target_bead": cause,
                "relationship": "caused_by",
                "confidence": 0.9,
            },
        )

        self.store.rebuild_index()

        post = self.store._read_json(self.store.beads_dir / "index.json")
        assoc = next(a for a in post.get("associations", []) if a.get("id") == "legacy-caused-by")
        self.assertEqual(cause, assoc.get("source_bead"))
        self.assertEqual(effect, assoc.get("target_bead"))
        self.assertEqual("causes", assoc.get("relationship"))
        self.assertEqual("caused_by", assoc.get("relationship_raw"))
        self.assertTrue(assoc.get("endpoints_swapped"))


if __name__ == "__main__":
    unittest.main()
