#!/usr/bin/env python3

import shutil
import tempfile
import unittest

from core_memory.store import MemoryStore


class TestSecretRedaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-secret-")
        self.store = MemoryStore(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_redacts_high_confidence_pat(self):
        bead_id = self.store.add_bead(
            type="context",
            title="Token github_pat_ABCDEF1234567890_ABCDEF1234567890 leaked",
            summary=["Contains x-access-token:supersecrettokenvalue123456@github.com"],
            session_id="s1",
        )
        bead = self.store.query(limit=1)[0]
        self.assertEqual(bead["id"], bead_id)
        self.assertIn("[REDACTED_SECRET:github_pat:", bead["title"])
        self.assertIn("[REDACTED_SECRET:x_access_token:", bead["summary"][0])

    def test_does_not_over_redact_normal_text(self):
        self.store.add_bead(
            type="context",
            title="Partial notions and artifacts",
            summary=["This is not a credential and should remain readable."],
            session_id="s1",
        )
        bead = self.store.query(limit=1)[0]
        self.assertEqual(bead["title"], "Partial notions and artifacts")
        self.assertEqual(bead["summary"][0], "This is not a credential and should remain readable.")


if __name__ == "__main__":
    unittest.main()
