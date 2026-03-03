#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.store import MemoryStore


class TestRationaleRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-rr-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_score_two_with_correct_id_and_rationale_overlap(self):
        bead_id = self.store.add_bead(
            type="decision",
            title="Use stdlib only",
            because=["reduce dependency risk", "simplify deployment"],
            summary=["zero external deps"],
            session_id="s1",
        )
        ans = f"We chose this because of dependency risk and deployment simplicity. See {bead_id}."
        r = self.store.evaluate_rationale_recall("Why did you decide to use stdlib?", ans, bead_id=bead_id)
        self.assertEqual(r["score"], 2)

    def test_score_zero_when_wrong_and_no_overlap(self):
        self.store.add_bead(
            type="decision",
            title="Use stdlib only",
            because=["reduce dependency risk"],
            session_id="s1",
        )
        r = self.store.evaluate_rationale_recall(
            "Why did you decide to use stdlib?",
            "Because vibes and aesthetics, no citation.",
        )
        self.assertEqual(r["score"], 0)


if __name__ == "__main__":
    unittest.main()
