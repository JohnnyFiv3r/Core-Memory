#!/usr/bin/env python3

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core_memory.store import MemoryStore


class TestEdgeTraversalLogging(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-edge-trace-"))
        self.store = MemoryStore(root=str(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recall_logs_edge_traversed_events(self):
        a = self.store.add_bead(type="decision", title="A", because=["x"], session_id="s1")
        b = self.store.add_bead(type="outcome", title="B", summary=["y"], session_id="s1")
        assoc_id = self.store.link(source_id=b, target_id=a, relationship="led_to", explanation="test")

        ok = self.store.recall(a)
        self.assertTrue(ok)

        events_file = self.tmp / ".beads" / "events" / "global.jsonl"
        self.assertTrue(events_file.exists())

        traversed = []
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("event_type") == "edge_traversed":
                    traversed.append(row)

        self.assertTrue(any((r.get("payload") or {}).get("edge_id") == assoc_id for r in traversed))


if __name__ == "__main__":
    unittest.main()
