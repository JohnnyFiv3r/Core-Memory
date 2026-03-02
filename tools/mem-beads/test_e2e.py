#!/usr/bin/env python3
"""End-to-end tests for mem-beads functionality."""

import os
import sys
import unittest
import tempfile
import shutil
import json

# PATCH 10.A: Use package import, not path hack
import mem_beads

TEST_DIR = "/tmp/mem-beads-e2e-test"
BEADS_DIR = os.path.join(TEST_DIR, ".mem-beads")


class TestBeadLifecycle(unittest.TestCase):
    """Test bead creation and lifecycle."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        # Fresh import
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_create_and_retrieve_bead(self):
        """Test basic bead creation and retrieval."""
        bead = self.mb.make_bead(
            bead_type="lesson",
            title="Test lesson",
            session_id="test-session",
            summary=["Key point 1", "Key point 2"],
            tags=["test", "e2e"]
        )
        self.mb.append_bead(bead)
        
        # Verify in index
        index = self.mb.load_index()
        self.assertIn(bead["id"], index["beads"])
        
        # Verify session tracking
        session = index["sessions"]["test-session"]
        self.assertEqual(session["bead_count"], 1)
        self.assertIn(bead["id"], session["bead_ids"])
        self.assertIsNotNone(session["started_at"])
        self.assertIsNotNone(session["ended_at"])
    
    def test_token_estimation(self):
        """Test token estimation is computed."""
        bead = self.mb.make_bead(
            bead_type="decision",
            title="Test decision",
            session_id="test",
            summary=["Item " + str(i) for i in range(10)]
        )
        self.mb.append_bead(bead)
        
        index = self.mb.load_index()
        session = index["sessions"]["test"]
        self.assertGreater(session["estimated_token_footprint"], 0)


class TestEdgeStore(unittest.TestCase):
    """Test edge store functionality."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_add_and_query_edges(self):
        """Test adding and querying edges."""
        # Create beads
        bead1 = self.mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = self.mb.make_bead(bead_type="outcome", title="O1", session_id="test")
        self.mb.append_bead(bead1)
        self.mb.append_bead(bead2)
        
        # Add edge: bead2 follows bead1 (bead1 caused bead2)
        edge = self.mb.add_edge(bead2["id"], bead1["id"], "follows")
        self.assertEqual(edge["source_id"], bead2["id"])
        self.assertEqual(edge["target_id"], bead1["id"])
        
        # Query edges
        edges_from = self.mb.get_edges_from(bead2["id"])
        self.assertEqual(len(edges_from), 1)
        
        edges_to = self.mb.get_edges_to(bead1["id"])
        self.assertEqual(len(edges_to), 1)
    
    def test_bead_with_links(self):
        """Test creating bead with links auto-creates edges."""
        bead1 = self.mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = self.mb.make_bead(
            bead_type="lesson",
            title="L1",
            session_id="test",
            links={"follows": [bead1["id"]], "context": [bead1["id"]]}
        )
        self.mb.append_bead(bead1)
        self.mb.append_bead(bead2)
        
        # Check edges were created - edges go FROM bead2 (source) TO bead1 (target)
        # So bead2 is source, bead1 is target
        edges = self.mb.get_edges_from(bead2["id"])
        self.assertEqual(len(edges), 2)
        
        edge_types = {e["type"] for e in edges}
        self.assertIn("follows", edge_types)
        self.assertIn("context", edge_types)
    
    def test_cycle_detection(self):
        """Test cycle detection blocks cyclic edges."""
        bead1 = self.mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = self.mb.make_bead(bead_type="decision", title="D2", session_id="test")
        self.mb.append_bead(bead1)
        self.mb.append_bead(bead2)
        
        # Add first edge
        self.mb.add_edge(bead2["id"], bead1["id"], "follows")
        
        # Adding reverse edge should raise
        with self.assertRaises(ValueError) as ctx:
            self.mb.add_edge(bead1["id"], bead2["id"], "follows")
        self.assertIn("cycle", str(ctx.exception).lower())
    
    def test_cyclic_types_allowed(self):
        """Test that cyclic link types allow cycles."""
        bead1 = self.mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = self.mb.make_bead(bead_type="decision", title="D2", session_id="test")
        self.mb.append_bead(bead1)
        self.mb.append_bead(bead2)
        
        # context is a cyclic type - should allow
        self.mb.add_edge(bead1["id"], bead2["id"], "context")
        self.mb.add_edge(bead2["id"], bead1["id"], "context")  # Should not raise


class TestChainQueries(unittest.TestCase):
    """Test chain query functionality."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_up_chain(self):
        """Test up chain traversal."""
        # Create chain: A <- B <- C
        bead_a = self.mb.make_bead(bead_type="decision", title="A", session_id="test")
        bead_b = self.mb.make_bead(bead_type="decision", title="B", session_id="test",
                                     links={"follows": [bead_a["id"]]})
        bead_c = self.mb.make_bead(bead_type="decision", title="C", session_id="test",
                                     links={"follows": [bead_b["id"]]})
        self.mb.append_bead(bead_a)
        self.mb.append_bead(bead_b)
        self.mb.append_bead(bead_c)
        
        # Up chain from C should find B then A
        chain = self.mb.get_up_chain(bead_c["id"])
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["bead_id"], bead_a["id"])  # Farthest first
        self.assertEqual(chain[1]["bead_id"], bead_b["id"])
    
    def test_down_chain(self):
        """Test down chain traversal."""
        # Create chain: A <- B <- C
        bead_a = self.mb.make_bead(bead_type="decision", title="A", session_id="test")
        bead_b = self.mb.make_bead(bead_type="decision", title="B", session_id="test",
                                     links={"follows": [bead_a["id"]]})
        bead_c = self.mb.make_bead(bead_type="decision", title="C", session_id="test",
                                     links={"follows": [bead_b["id"]]})
        self.mb.append_bead(bead_a)
        self.mb.append_bead(bead_b)
        self.mb.append_bead(bead_c)
        
        # Down chain from A should find B then C
        chain = self.mb.get_down_chain(bead_a["id"])
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["bead_id"], bead_b["id"])  # Nearest first
        self.assertEqual(chain[1]["bead_id"], bead_c["id"])
    
    def test_context_chain(self):
        """Test context chain traversal."""
        bead1 = self.mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = self.mb.make_bead(bead_type="lesson", title="L1", session_id="test",
                                    links={"context": [bead1["id"]]})
        self.mb.append_bead(bead1)
        self.mb.append_bead(bead2)
        
        chain = self.mb.get_context_chain(bead2["id"])
        self.assertEqual(len(chain), 1)


class TestLifecycleStateMachine(unittest.TestCase):
    """Test lifecycle state machine."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_valid_transitions(self):
        """Test valid state transitions."""
        self.assertTrue(self.mb.can_transition("open", "closed"))
        self.assertTrue(self.mb.can_transition("open", "promoted"))
        self.assertTrue(self.mb.can_transition("open", "compacted"))
        self.assertTrue(self.mb.can_transition("compacted", "promoted"))
    
    def test_invalid_transitions(self):
        """Test invalid state transitions are blocked."""
        self.assertFalse(self.mb.can_transition("open", "tombstoned"))
        self.assertFalse(self.mb.can_transition("tombstoned", "open"))
        self.assertFalse(self.mb.can_transition("promoted", "open"))
    
    def test_transition_bead(self):
        """Test transitioning a bead."""
        bead = self.mb.make_bead(bead_type="lesson", title="Test", session_id="test")
        self.mb.append_bead(bead)
        
        # Transition to compacted
        result = self.mb.transition_bead(bead["id"], "compacted", "test")
        self.assertEqual(result["status"], "compacted")
        self.assertIsNotNone(result.get("status_changed_at"))
    
    def test_pin_bead(self):
        """Test pinning beads."""
        bead = self.mb.make_bead(bead_type="lesson", title="Test", session_id="test")
        self.mb.append_bead(bead)
        
        # Pin
        result = self.mb.pin_bead(bead["id"])
        self.assertTrue(result["pinned"])
        
        # Unpin
        result = self.mb.unpin_bead(bead["id"])
        self.assertFalse(result["pinned"])


class TestContextPacket(unittest.TestCase):
    """Test context packet builder."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_build_context_packet(self):
        """Test building context packet."""
        # Create beads
        for i in range(5):
            bead = self.mb.make_bead(
                bead_type="lesson" if i % 2 == 0 else "decision",
                title=f"Bead {i}",
                session_id="test",
                tags=["test"]
            )
            self.mb.append_bead(bead)
        
        # PATCH 10: Test uses budget-driven selection with low budget to enforce limit
        # Set budget low to test budget enforcement (each bead ~300 tokens)
        old_budget = os.environ.get("MEMBEADS_CONTEXT_BUDGET")
        os.environ["MEMBEADS_CONTEXT_BUDGET"] = "500"  # ~1-2 beads max
        
        try:
            packet = self.mb.build_context_packet(tags=["test"], use_rolling_window=False)
            
            self.assertIn("version", packet)
            self.assertIn("generated_at", packet)
            self.assertIn("stats", packet)
            self.assertIn("beads", packet)
            self.assertEqual(packet["stats"]["total_matched"], 5)
            # With budget=500 and each bead ~300 tokens, should get 1-2 beads
            self.assertLessEqual(packet["stats"]["beads_included"], 2)
        finally:
            if old_budget:
                os.environ["MEMBEADS_CONTEXT_BUDGET"] = old_budget
            else:
                os.environ.pop("MEMBEADS_CONTEXT_BUDGET", None)
    
    def test_packet_no_mutations(self):
        """Test that building packet doesn't mutate store."""
        bead = self.mb.make_bead(bead_type="lesson", title="Test", session_id="test")
        self.mb.append_bead(bead)
        
        index_before = json.dumps(self.mb.load_index())
        
        # Build packet
        self.mb.build_context_packet()
        
        index_after = json.dumps(self.mb.load_index())
        
        # Should be identical
        self.assertEqual(index_before, index_after)


class TestRollingWindow(unittest.TestCase):
    """Test rolling window algorithm."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_compute_rolling_window(self):
        """Test rolling window computation."""
        # Create beads in multiple sessions
        for session in ["old", "middle", "new"]:
            for i in range(3):
                bead = self.mb.make_bead(
                    bead_type="lesson",
                    title=f"{session} bead {i}",
                    session_id=session,
                    summary=["point"] * 3
                )
                self.mb.append_bead(bead)
        
        # Compute window
        result = self.mb.compute_rolling_window(context_budget=1000)
        
        self.assertIn("sessions", result)
        self.assertIn("stats", result)
        # Should include sessions
        self.assertGreater(len(result["sessions"]), 0)
    
    def test_token_budget_config(self):
        """Test token budget configuration."""
        config = self.mb.get_token_budget_config()
        
        self.assertIn("context_budget_tokens", config)
        self.assertIn("max_session_tokens", config)
        self.assertIn("min_sessions_keep", config)


class TestSessionTriggers(unittest.TestCase):
    """Test session trigger functions."""
    
    @classmethod
    def setUpClass(cls):
        cls.original_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        import mem_beads
        cls.mb = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        if cls.original_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    
    def test_on_session_close(self):
        """Test session close trigger."""
        # Create beads
        for i in range(3):
            bead = self.mb.make_bead(bead_type="lesson", title=f"Bead {i}", session_id="test-close")
            self.mb.append_bead(bead)
        
        # Close session
        result = self.mb.on_session_close("test-close")
        
        self.assertEqual(result["action"], "on_session_close")
        # Beads should be compacted
        index = self.mb.load_index()
        for bead_id in index["sessions"]["test-close"]["bead_ids"]:
            status = index["beads"][bead_id]["status"]
            self.assertIn(status, ["compacted", "promoted"])
    
    def test_pinned_beads_not_compacted(self):
        """Test that pinned beads are not compacted."""
        bead = self.mb.make_bead(bead_type="lesson", title="Pinned", session_id="test-pin")
        self.mb.append_bead(bead)
        
        # Pin the bead
        self.mb.pin_bead(bead["id"])
        
        # Close session
        result = self.mb.on_session_close("test-pin")
        
        # Check bead was NOT compacted
        index = self.mb.load_index()
        status = index["beads"][bead["id"]]["status"]
        self.assertEqual(status, "open")  # Pinned stays open


if __name__ == "__main__":
    unittest.main(verbosity=2)
