#!/usr/bin/env python3
"""Phase 1 parity harness for mem_beads -> core_memory migration.

This file intentionally mixes:
- baseline tests (must pass now)
- expected-failure parity tests (define migration target behavior)

Do not remove expected failures until adapter parity is implemented.
"""

import importlib
import json
import os
import shutil
import tempfile
import threading
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
        importlib.reload(mem_beads)

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

    def test_mem_beads_context_packet_deterministic(self):
        """Phase 1 requirement: packet assembly is deterministic for same store+filters."""
        b1 = mem_beads.make_bead("decision", "Use stdlib", "phase1", tags=["ctx"])
        b2 = mem_beads.make_bead("outcome", "Implemented", "phase1", tags=["ctx"])
        mem_beads.append_bead(b1)
        mem_beads.append_bead(b2)
        mem_beads.add_edge(b2["id"], b1["id"], "derives-from")

        p1 = mem_beads.build_context_packet(session_ids=["phase1"], limit=20, include_chains=True, use_rolling_window=False)
        p2 = mem_beads.build_context_packet(session_ids=["phase1"], limit=20, include_chains=True, use_rolling_window=False)

        # Ignore run timestamp field; compare structural determinism
        p1_norm = dict(p1)
        p2_norm = dict(p2)
        p1_norm.pop("generated_at", None)
        p2_norm.pop("generated_at", None)

        self.assertEqual(
            json.dumps(p1_norm, sort_keys=True),
            json.dumps(p2_norm, sort_keys=True),
            "Context packet should be deterministic for same inputs",
        )

    def test_mem_beads_edge_write_concurrency_no_corruption(self):
        """Phase 1 requirement: concurrent edge writes should not corrupt edges.jsonl."""
        # create beads used by concurrent writers
        beads = []
        for i in range(12):
            bead = mem_beads.make_bead("decision", f"D{i}", "phase1", tags=["concurrency"])
            mem_beads.append_bead(bead)
            beads.append(bead)

        errors = []

        def worker(start_idx):
            try:
                for j in range(start_idx, min(start_idx + 4, len(beads) - 1)):
                    mem_beads.add_edge(beads[j + 1]["id"], beads[j]["id"], "follows")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 3, 6, 8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertFalse(errors, f"Concurrent add_edge errors: {errors}")

        # Validate file parseability (no torn JSON lines)
        edges = mem_beads.get_all_edges()
        self.assertGreaterEqual(len(edges), 4)
        for e in edges:
            self.assertIn("source_id", e)
            self.assertIn("target_id", e)
            self.assertIn("type", e)

    def test_env_compat_membeads_dir_fallback(self):
        """Phase 1 requirement: MEMBEADS_DIR fallback remains valid."""
        fallback_root = os.path.join(self.tmp, "fallback-root")
        os.makedirs(fallback_root, exist_ok=True)

        old_root = os.environ.pop("MEMBEADS_ROOT", None)
        old_dir = os.environ.get("MEMBEADS_DIR")
        os.environ["MEMBEADS_DIR"] = fallback_root

        try:
            mod = importlib.reload(mem_beads)
            self.assertEqual(mod.MEMBEADS_ROOT, fallback_root)

            bead = mod.make_bead("context", "Fallback test", "phase1")
            mod.append_bead(bead)
            idx_path = os.path.join(fallback_root, "index.json")
            self.assertTrue(os.path.exists(idx_path))
        finally:
            if old_root is not None:
                os.environ["MEMBEADS_ROOT"] = old_root
            else:
                os.environ.pop("MEMBEADS_ROOT", None)

            if old_dir is not None:
                os.environ["MEMBEADS_DIR"] = old_dir
            else:
                os.environ.pop("MEMBEADS_DIR", None)
            importlib.reload(mem_beads)


if __name__ == "__main__":
    unittest.main()
