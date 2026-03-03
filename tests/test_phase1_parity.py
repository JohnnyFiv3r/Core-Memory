#!/usr/bin/env python3
"""Core migration parity/hardening tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from core_memory.store import MemoryStore


class TestCoreMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-mig-")
        self.core_root = os.path.join(self.tmp, "core")
        os.makedirs(self.core_root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_legacy_store(self, path):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "session-s1.jsonl"), "w") as f:
            f.write(json.dumps({
                "id": "bead-LEGACYA", "type": "decision", "created_at": "2026-03-02T00:00:00+00:00",
                "session_id": "s1", "title": "Legacy A", "summary": ["a"], "detail": "", "scope": "project",
                "authority": "agent_inferred", "confidence": 0.8, "tags": ["legacy"], "status": "open"
            }) + "\n")
            f.write(json.dumps({
                "id": "bead-LEGACYB", "type": "decision", "created_at": "2026-03-02T00:01:00+00:00",
                "session_id": "s1", "title": "Legacy B", "summary": ["b"], "detail": "", "scope": "project",
                "authority": "agent_inferred", "confidence": 0.8, "tags": ["legacy"], "status": "open"
            }) + "\n")
        with open(os.path.join(path, "edges.jsonl"), "w") as f:
            f.write(json.dumps({
                "id": "edge-LEGACY1", "source_id": "bead-LEGACYB", "target_id": "bead-LEGACYA",
                "type": "follows", "created_at": "2026-03-02T00:02:00+00:00"
            }) + "\n")
        with open(os.path.join(path, "index.json"), "w") as f:
            json.dump({
                "beads": {
                    "bead-LEGACYA": {"type": "decision", "session_id": "s1", "status": "open", "title": "Legacy A", "file": "session-s1.jsonl", "line": 0, "created_at": "2026-03-02T00:00:00+00:00", "tags": ["legacy"], "scope": "project"},
                    "bead-LEGACYB": {"type": "decision", "session_id": "s1", "status": "open", "title": "Legacy B", "file": "session-s1.jsonl", "line": 1, "created_at": "2026-03-02T00:01:00+00:00", "tags": ["legacy"], "scope": "project"}
                },
                "stats": {"total_beads": 2}
            }, f)

    def test_add_query_contract(self):
        store = MemoryStore(root=self.core_root)
        bead_id = store.add_bead(type="decision", title="Contract", session_id="s1", tags=["t"])
        self.assertTrue(bead_id.startswith("bead-"))
        rows = store.query(type="decision", tags=["t"], limit=10)
        self.assertTrue(any(r.get("id") == bead_id for r in rows))

    def test_migrate_store_idempotent(self):
        legacy = os.path.join(self.tmp, "legacy")
        self._seed_legacy_store(legacy)

        cmd = [sys.executable, "-m", "core_memory.cli", "--root", self.core_root, "migrate-store", "--legacy-root", legacy]
        r1 = subprocess.run(cmd, capture_output=True, text=True)
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        p1 = json.loads(r1.stdout)
        p2 = json.loads(r2.stdout)
        self.assertGreaterEqual(p1.get("imported_beads", 0), 2)
        self.assertGreaterEqual(p1.get("imported_associations", 0), 1)
        self.assertEqual(p2.get("imported_beads", 0), 0)
        self.assertEqual(p2.get("imported_associations", 0), 0)


if __name__ == "__main__":
    unittest.main()
