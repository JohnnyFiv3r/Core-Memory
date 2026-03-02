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
import subprocess
import sys
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

    def test_phase2_core_adapter_smoke_add_query(self):
        """Phase 2 scaffold: mem-beads CLI can route to core_memory via opt-in flag."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        add_cmd = [
            sys.executable,
            "-m",
            "mem_beads",
            "add",
            "--type",
            "decision",
            "--title",
            "Adapter Path",
            "--session-id",
            "phase2",
        ]
        add_run = subprocess.run(add_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(add_run.returncode, 0, add_run.stderr)
        self.assertIn("Created bead:", add_run.stdout)

        query_cmd = [
            sys.executable,
            "-m",
            "mem_beads",
            "query",
            "--type",
            "decision",
            "--limit",
            "5",
        ]
        query_run = subprocess.run(query_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(query_run.returncode, 0, query_run.stderr)
        self.assertIn("[decision]", query_run.stdout)

    def test_phase2_core_adapter_translates_legacy_create(self):
        """Legacy `create --session --tags a,b` should be translated for core CLI."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        cmd = [
            sys.executable,
            "-m",
            "mem_beads",
            "create",
            "--type",
            "decision",
            "--title",
            "Legacy Create",
            "--session",
            "phase2",
            "--tags",
            "a,b",
        ]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Created bead:", run.stdout)

        store = MemoryStore(root=self.core_root)
        rows = store.query(type="decision", tags=["a"], limit=10)
        self.assertTrue(any(r.get("title") == "Legacy Create" for r in rows))

    def test_phase2_core_adapter_link_direct_handler(self):
        """`link` should be handled directly by core adapter when flag enabled."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        d = store.add_bead(type="decision", title="D", session_id="phase2")
        o = store.add_bead(type="outcome", title="O", session_id="phase2")

        cmd = [
            sys.executable, "-m", "mem_beads", "link",
            "--from", o, "--to", d, "--type", "follows"
        ]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertTrue(payload.get("ok"))

        rows = store._read_json(store.beads_dir / "index.json").get("associations", [])
        self.assertTrue(any(a.get("source_bead") == o and a.get("target_bead") == d for a in rows))

    def test_phase2_core_adapter_recall_direct_handler(self):
        """`recall` should route through core adapter and increment recall_count."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        bead_id = store.add_bead(type="decision", title="Recall me", session_id="phase2")

        cmd = [sys.executable, "-m", "mem_beads", "recall", "--id", bead_id]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

        bead = store._read_json(store.beads_dir / "index.json")["beads"][bead_id]
        self.assertEqual(bead.get("recall_count"), 1)

    def test_phase2_core_adapter_supersede_direct_handler(self):
        """`supersede` should be handled by core adapter and create supersedes association."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        old_id = store.add_bead(type="decision", title="Old", session_id="phase2")
        new_id = store.add_bead(type="decision", title="New", session_id="phase2")

        cmd = [
            sys.executable, "-m", "mem_beads", "supersede",
            "--old", old_id, "--new", new_id
        ]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual(payload.get("status"), "superseded")

        rows = store._read_json(store.beads_dir / "index.json").get("associations", [])
        self.assertTrue(any(a.get("source_bead") == new_id and a.get("target_bead") == old_id and a.get("relationship") == "supersedes" for a in rows))

    def test_phase2_core_adapter_validate_direct_handler(self):
        """`validate` should return expected contract payload from core adapter."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        store.add_bead(type="decision", title="V", session_id="phase2")

        cmd = [sys.executable, "-m", "mem_beads", "validate"]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertTrue(payload.get("ok"))
        self.assertIn("total_beads", payload)
        self.assertIn("total_edges", payload)

    def test_phase2_core_adapter_fallback_for_unsupported_commands(self):
        """Unsupported commands should fallback to legacy implementation under adapter flag."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.mem_root

        # `myelinate` is not supported by core adapter yet -> fallback to legacy path
        cmd = [sys.executable, "-m", "mem_beads", "myelinate"]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertIn("dry_run", payload)

    def test_phase2_core_adapter_fallback_compact_uncompact(self):
        """compact/uncompact remain legacy-routed under adapter flag."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.mem_root

        # Seed legacy store
        legacy_env = env.copy()
        legacy_env.pop("MEMBEADS_USE_CORE_ADAPTER", None)
        c = [
            sys.executable, "-m", "mem_beads", "create",
            "--type", "decision", "--title", "to-compact", "--session", "s1"
        ]
        cr = subprocess.run(c, cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stderr)

        compact_cmd = [sys.executable, "-m", "mem_beads", "compact", "--session", "s1"]
        compact_run = subprocess.run(compact_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(compact_run.returncode, 0, compact_run.stderr)
        compact_payload = json.loads(compact_run.stdout)
        self.assertTrue(compact_payload.get("ok"))

        uncompact_cmd = [sys.executable, "-m", "mem_beads", "uncompact", "--id", "bead-missing"]
        uncompact_run = subprocess.run(uncompact_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(uncompact_run.returncode, 1)
        uncompact_payload = json.loads(uncompact_run.stdout)
        self.assertFalse(uncompact_payload.get("ok", True))

    def test_phase2_core_adapter_close_non_promoted_falls_back(self):
        """close status != promoted should fallback to legacy behavior."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.mem_root

        cmd = [
            sys.executable, "-m", "mem_beads", "close",
            "--id", "bead-missing", "--status", "closed"
        ]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        payload = json.loads(run.stdout)
        self.assertFalse(payload.get("ok", True))

    def test_phase2_core_adapter_compact_uncompact_core_native(self):
        """compact/uncompact should route to core-native implementation."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        bead_id = store.add_bead(type="decision", title="C", detail="long detail", session_id="s1")

        compact_cmd = [
            sys.executable, "-m", "mem_beads", "compact", "--session", "s1", "--keep-promoted"
        ]
        cr = subprocess.run(compact_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stderr)
        cp = json.loads(cr.stdout)
        self.assertTrue(cp.get("ok"))

        bead = store._read_json(store.beads_dir / "index.json")["beads"][bead_id]
        self.assertEqual(bead.get("detail"), "")
        self.assertEqual(bead.get("status"), "promoted")

        uncompact_cmd = [sys.executable, "-m", "mem_beads", "uncompact", "--id", bead_id]
        ur = subprocess.run(uncompact_cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(ur.returncode, 0, ur.stderr)
        up = json.loads(ur.stdout)
        self.assertTrue(up.get("ok"))

        bead2 = store._read_json(store.beads_dir / "index.json")["beads"][bead_id]
        self.assertEqual(bead2.get("detail"), "long detail")

    def test_phase2_core_adapter_myelinate_core_native(self):
        """myelinate should use core-native deterministic payload."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        store = MemoryStore(root=self.core_root)
        bead_id = store.add_bead(type="decision", title="M", session_id="s1")
        store.recall(bead_id)
        store.recall(bead_id)
        store.recall(bead_id)

        cmd = [sys.executable, "-m", "mem_beads", "myelinate"]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertIn("dry_run", payload)
        self.assertIn("actions", payload)

    def test_phase2_core_adapter_migrate_store_command(self):
        """migrate-store should import legacy index + edges into core index."""
        legacy_root = os.path.join(self.tmp, "legacy-store")
        os.makedirs(legacy_root, exist_ok=True)

        legacy_env = os.environ.copy()
        legacy_env["MEMBEADS_ROOT"] = legacy_root
        legacy_env.pop("MEMBEADS_USE_CORE_ADAPTER", None)

        c1 = [
            sys.executable, "-m", "mem_beads", "create",
            "--type", "decision", "--title", "Legacy A", "--session", "s1"
        ]
        c2 = [
            sys.executable, "-m", "mem_beads", "create",
            "--type", "decision", "--title", "Legacy B", "--session", "s1"
        ]
        r1 = subprocess.run(c1, cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)
        r2 = subprocess.run(c2, cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)
        a = json.loads(r1.stdout)["id"]
        b = json.loads(r2.stdout)["id"]
        link = [sys.executable, "-m", "mem_beads", "link", "--from", b, "--to", a, "--type", "follows"]
        subprocess.run(link, cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)

        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root
        mig = [
            sys.executable, "-m", "mem_beads", "migrate-store", "--legacy-root", legacy_root
        ]
        mr = subprocess.run(mig, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(mr.returncode, 0, mr.stderr)
        payload = json.loads(mr.stdout)
        self.assertTrue(payload.get("ok"))
        self.assertGreaterEqual(payload.get("imported_beads", 0), 2)

    def test_phase3_migrate_store_idempotent(self):
        """Running migrate-store twice should not duplicate imported beads."""
        legacy_root = os.path.join(self.tmp, "legacy-idempotent")
        os.makedirs(legacy_root, exist_ok=True)

        legacy_env = os.environ.copy()
        legacy_env["MEMBEADS_ROOT"] = legacy_root
        legacy_env.pop("MEMBEADS_USE_CORE_ADAPTER", None)

        subprocess.run([
            sys.executable, "-m", "mem_beads", "create",
            "--type", "decision", "--title", "One", "--session", "s1"
        ], cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)

        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        mig = [sys.executable, "-m", "mem_beads", "migrate-store", "--legacy-root", legacy_root]
        r1 = subprocess.run(mig, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        r2 = subprocess.run(mig, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)

        p1 = json.loads(r1.stdout)
        p2 = json.loads(r2.stdout)
        self.assertGreaterEqual(p1.get("imported_beads", 0), 1)
        self.assertEqual(p2.get("imported_beads", 0), 0)

    def test_phase3_migrate_store_backup_created(self):
        """migrate-store should create a backup of core index by default."""
        legacy_root = os.path.join(self.tmp, "legacy-backup")
        os.makedirs(legacy_root, exist_ok=True)

        legacy_env = os.environ.copy()
        legacy_env["MEMBEADS_ROOT"] = legacy_root
        legacy_env.pop("MEMBEADS_USE_CORE_ADAPTER", None)
        subprocess.run([
            sys.executable, "-m", "mem_beads", "create",
            "--type", "decision", "--title", "L", "--session", "s1"
        ], cwd=os.getcwd(), env=legacy_env, capture_output=True, text=True)

        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root
        mig = [sys.executable, "-m", "mem_beads", "migrate-store", "--legacy-root", legacy_root]
        run = subprocess.run(mig, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

        backups = [p for p in os.listdir(os.path.join(self.core_root, ".beads")) if p.startswith("index.backup.")]
        self.assertTrue(backups)

    def test_phase3_migrate_store_missing_legacy_fails_cleanly(self):
        """Missing legacy path should fail with a clear error and non-zero code."""
        env = os.environ.copy()
        env["MEMBEADS_USE_CORE_ADAPTER"] = "1"
        env["MEMBEADS_ROOT"] = self.core_root

        missing = os.path.join(self.tmp, "does-not-exist")
        cmd = [sys.executable, "-m", "mem_beads", "migrate-store", "--legacy-root", missing]
        run = subprocess.run(cmd, cwd=os.getcwd(), env=env, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Legacy index not found", run.stderr)

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
