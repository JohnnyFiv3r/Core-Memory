#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.persistence.store import MemoryStore


class TestDecisionConflicts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-conflict-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_unjustified_flip(self):
        prior = self.store.add_bead(
            type="decision",
            title="Use redis cache for sessions",
            because=["performance"],
            session_id="s1",
        )
        new_id = self.store.add_bead(
            type="decision",
            title="Do not use redis cache for sessions",
            because=["operational risk"],
            session_id="s1",
        )
        idx = self.store._read_json(self.store.beads_dir / "index.json")
        b = idx["beads"][new_id]
        self.assertTrue(b.get("unjustified_flip"))
        self.assertIn(prior, b.get("decision_conflict_with", []))

    def test_no_conflict_when_prior_superseded(self):
        prior = self.store.add_bead(
            type="decision",
            title="Use sqlite for queue",
            because=["simplicity"],
            session_id="s1",
        )
        # mark prior superseded
        idx = self.store._read_json(self.store.beads_dir / "index.json")
        idx["beads"][prior]["status"] = "superseded"
        self.store._write_json(self.store.beads_dir / "index.json", idx)

        new_id = self.store.add_bead(
            type="decision",
            title="Do not use sqlite for queue",
            because=["throughput needs"],
            session_id="s1",
        )
        idx2 = self.store._read_json(self.store.beads_dir / "index.json")
        b2 = idx2["beads"][new_id]
        self.assertFalse(b2.get("unjustified_flip", False))


if __name__ == "__main__":
    unittest.main()
