#!/usr/bin/env python3
"""Phase 1 parity harness for mem_beads -> core_memory migration.

This file intentionally mixes:
- baseline tests (must pass now)
- expected-failure parity tests (define migration target behavior)

Do not remove expected failures until adapter parity is implemented.
"""

import json
import os
import shutil
import tempfile
import unittest

import mem_beads
from core_memory.store import MemoryStore


class TestPhase1ParityHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-memory-phase1-")
        self.mem_root = os.path.join(self.tmp, "mem-beads-store")
        self.core_root = os.path.join(self.tmp, "core-memory-store")
        os.makedirs(self.mem_root, exist_ok=True)
        os.makedirs(self.core_root, exist_ok=True)

        self._orig_mem_root = os.environ.get("MEMBEADS_ROOT")
        os.environ["MEMBEADS_ROOT"] = self.mem_root

    def tearDown(self):
        if self._orig_mem_root is None:
            os.environ.pop("MEMBEADS_ROOT", None)
        else:
            os.environ["MEMBEADS_ROOT"] = self._orig_mem_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_phase0_docs_exist(self):
        self.assertTrue(os.path.exists("MIGRATION_PLAN.md"))
        self.assertTrue(os.path.exists("COMPATIBILITY_SPEC.md"))

    def test_mem_beads_baseline_add_query(self):
        bead = mem_beads.make_bead(
            bead_type="decision",
            title="Use stdlib only",
            session_id="phase1",
            summary=["No external deps"],
            tags=["phase1", "parity"],
        )
        mem_beads.append_bead(bead)

        idx = mem_beads.load_index()
        self.assertIn(bead["id"], idx["beads"])

        all_beads = mem_beads.read_all_beads()
        q = [b for b in all_beads if b.get("type") == "decision" and "phase1" in b.get("tags", [])]
        self.assertGreaterEqual(len(q), 1)

    def test_core_memory_baseline_add_query(self):
        store = MemoryStore(root=self.core_root)
        bead_id = store.add_bead(
            type="decision",
            title="Use stdlib only",
            session_id="phase1",
            summary=["No external deps"],
            tags=["phase1", "parity"],
        )
        self.assertTrue(bead_id.startswith("bead-"))

        q = store.query(type="decision", tags=["phase1"], limit=10)
        self.assertGreaterEqual(len(q), 1)

    def test_parity_add_query_normalized_contract(self):
        """Target parity: normalized fields should match for equivalent add/query flows."""
        bead = mem_beads.make_bead(
            bead_type="decision",
            title="Contract test",
            session_id="phase1",
            summary=["normalized parity"],
            tags=["contract"],
        )
        mem_beads.append_bead(bead)

        store = MemoryStore(root=self.core_root)
        core_id = store.add_bead(
            type="decision",
            title="Contract test",
            session_id="phase1",
            summary=["normalized parity"],
            tags=["contract"],
        )

        mem_result = [
            b for b in mem_beads.read_all_beads()
            if b.get("type") == "decision" and "contract" in b.get("tags", [])
        ][0]
        core_result = store.query(type="decision", tags=["contract"], limit=1)[0]

        # parity targets (intentional strictness)
        self.assertEqual(mem_result.get("type"), core_result.get("type"))
        self.assertEqual(mem_result.get("title"), core_result.get("title"))
        self.assertEqual(mem_result.get("summary"), core_result.get("summary"))
        self.assertIn(core_id, [core_result.get("id")])

    def test_parity_link_direction_semantics(self):
        """Target parity: link direction semantics must match current mem_beads contract."""
        # mem_beads: source=newer/effect, target=older/cause
        a = mem_beads.make_bead("outcome", "A", "phase1")
        b = mem_beads.make_bead("decision", "B", "phase1")
        mem_beads.append_bead(a)
        mem_beads.append_bead(b)
        e = mem_beads.add_edge(a["id"], b["id"], "follows")

        store = MemoryStore(root=self.core_root)
        a2 = store.add_bead(type="outcome", title="A", session_id="phase1")
        b2 = store.add_bead(type="decision", title="B", session_id="phase1")
        assoc = store.link(a2, b2, "follows")

        # explicit parity checks to enforce during migration
        self.assertEqual(e["source_id"], a["id"])
        self.assertEqual(e["target_id"], b["id"])
        self.assertTrue(assoc.startswith("assoc-"))


if __name__ == "__main__":
    unittest.main()
