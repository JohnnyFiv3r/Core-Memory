#!/usr/bin/env python3
"""Tests for mem-beads edge store functionality."""

import os
import sys
import unittest
import tempfile
import shutil

# PATCH 10.A: Use package import, not path hack
import mem_beads

# Test configuration
TEST_DIR = "/tmp/mem-beads-test"
BEADS_DIR = os.path.join(TEST_DIR, ".mem-beads")


class TestEdgeStore(unittest.TestCase):
    """Test edge store functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Create test environment."""
        # Save original env
        cls.original_mem_beads_dir = os.environ.get("MEMBEADS_ROOT")
        
        # Create test directory
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        # PATCH 10.A: Clear module cache so env var is re-read
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        
        # Import after setting env
        import mem_beads
        cls.mem_beads = mem_beads
        
        # Clear any existing edges
        edges_path = os.path.join(BEADS_DIR, "edges.jsonl")
        if os.path.exists(edges_path):
            os.remove(edges_path)
    
    @classmethod
    def tearDownClass(cls):
        """Cleanup."""
        if cls.original_mem_beads_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_mem_beads_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        
        # Cleanup test dir
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR)
    
    def setUp(self):
        """Clear edges before each test."""
        edges_path = os.path.join(BEADS_DIR, "edges.jsonl")
        if os.path.exists(edges_path):
            os.remove(edges_path)
    
    def test_link_types_defined(self):
        """Verify all link types are defined."""
        mb = self.mem_beads
        self.assertIn("follows", mb.LINK_TYPES)
        self.assertIn("derives-from", mb.LINK_TYPES)
        self.assertIn("supersedes", mb.LINK_TYPES)
        self.assertIn("extends", mb.LINK_TYPES)
        self.assertIn("responds-to", mb.LINK_TYPES)
        self.assertIn("continues", mb.LINK_TYPES)
        self.assertIn("validates", mb.LINK_TYPES)
        self.assertIn("revises", mb.LINK_TYPES)
        self.assertIn("context", mb.LINK_TYPES)
        self.assertIn("related", mb.LINK_TYPES)
        self.assertIn("recalls", mb.LINK_TYPES)
    
    def test_acyclic_types_defined(self):
        """Verify acyclic link types are defined."""
        mb = self.mem_beads
        self.assertEqual(mb.ACYCLIC_LINK_TYPES, {"follows", "derives-from", "extends", "supersedes", "revises"})
    
    def test_add_edge_basic(self):
        """Test basic edge creation."""
        mb = self.mem_beads
        edge = mb.add_edge("bead-A", "bead-B", "follows")
        
        self.assertIsNotNone(edge)
        self.assertEqual(edge["source_id"], "bead-A")
        self.assertEqual(edge["target_id"], "bead-B")
        self.assertEqual(edge["type"], "follows")
        self.assertEqual(edge["scope"], "session")
    
    def test_add_edge_invalid_type(self):
        """Test adding edge with invalid type raises error."""
        mb = self.mem_beads
        with self.assertRaises(ValueError):
            mb.add_edge("bead-A", "bead-B", "invalid-type")
    
    def test_find_edge(self):
        """Test finding an edge."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        
        edge = mb.find_edge("bead-A", "bead-B", "follows")
        self.assertIsNotNone(edge)
        
        # Non-existent
        edge = mb.find_edge("bead-A", "bead-C", "follows")
        self.assertIsNone(edge)
    
    def test_get_edges_from(self):
        """Test getting edges from a bead."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        mb.add_edge("bead-A", "bead-C", "derives-from")
        
        edges = mb.get_edges_from("bead-A")
        self.assertEqual(len(edges), 2)
    
    def test_get_edges_to(self):
        """Test getting edges to a bead."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        mb.add_edge("bead-C", "bead-B", "derives-from")
        
        edges = mb.get_edges_to("bead-B")
        self.assertEqual(len(edges), 2)
    
    def test_get_neighbors(self):
        """Test getting all neighbors."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        
        neighbors = mb.get_neighbors("bead-A")
        self.assertEqual(len(neighbors["from"]), 1)
        self.assertEqual(len(neighbors["to"]), 0)
        
        neighbors = mb.get_neighbors("bead-B")
        self.assertEqual(len(neighbors["from"]), 0)
        self.assertEqual(len(neighbors["to"]), 1)
    
    def test_cycle_detection_follows(self):
        """Test cycle detection for follows type."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        
        # This would create a cycle: A -> B -> A
        with self.assertRaises(ValueError) as context:
            mb.add_edge("bead-B", "bead-A", "follows")
        
        self.assertIn("cycle", str(context.exception).lower())
    
    def test_cycle_detection_derives_from(self):
        """Test cycle detection for derives-from type."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "derives-from")
        
        # This would create a cycle
        with self.assertRaises(ValueError):
            mb.add_edge("bead-B", "bead-A", "derives-from")
    
    def test_no_cycle_different_types(self):
        """Test that different link types don't create cycles."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows")
        
        # Different type should be allowed
        edge = mb.add_edge("bead-B", "bead-A", "derives-from")
        self.assertIsNotNone(edge)
    
    def test_cyclic_types_allowed(self):
        """Test that cyclic types can form cycles."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "context")
        
        # Should be allowed
        edge = mb.add_edge("bead-B", "bead-A", "context")
        self.assertIsNotNone(edge)
    
    def test_unique_edge(self):
        """Test unique edge constraint."""
        mb = self.mem_beads
        mb.add_edge("bead-A", "bead-B", "follows", metadata={"note": "first"})
        
        # Add same edge again with different metadata
        edge = mb.add_edge("bead-A", "bead-B", "follows", metadata={"note": "updated"})
        
        # Should update, not create new
        edges = mb.get_edges_from("bead-A")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["metadata"]["note"], "updated")
    
    def test_scope_metadata(self):
        """Test edge scope and metadata."""
        mb = self.mem_beads
        edge = mb.add_edge(
            "bead-A", "bead-B", "follows",
            scope="cross_session",
            thread_id="thread-001",
            metadata={"confidence": 0.9}
        )
        
        self.assertEqual(edge["scope"], "cross_session")
        self.assertEqual(edge["thread_id"], "thread-001")
        self.assertEqual(edge["metadata"]["confidence"], 0.9)


class TestChainQueries(unittest.TestCase):
    """Test chain query functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Create test environment."""
        cls.original_mem_beads_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        import mem_beads
        cls.mem_beads = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        """Cleanup."""
        if cls.original_mem_beads_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_mem_beads_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR)
    
    def setUp(self):
        """Clear edges before each test."""
        edges_path = os.path.join(BEADS_DIR, "edges.jsonl")
        if os.path.exists(edges_path):
            os.remove(edges_path)
    
    def test_up_chain_simple(self):
        """Test simple up chain."""
        mb = self.mem_beads
        # Code uses: source = newer/current bead, target = older/linked bead
        # "A follows B" means A (newer) was caused by B (older)
        # So edge: source=A (newer), target=B (older)
        # Add: bead-A -> bead-B -> bead-C (where A is newest, C is oldest)
        mb.add_edge("bead-B", "bead-C", "follows")  # B caused by C (B newer than C)
        mb.add_edge("bead-A", "bead-B", "follows")  # A caused by B (A newest)
        
        # Up chain from A should find B then C (causes of A)
        # Code returns farthest first
        chain = mb.get_up_chain("bead-A")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["bead_id"], "bead-C")  # Farthest first
        self.assertEqual(chain[0]["distance"], 2)
        self.assertEqual(chain[1]["bead_id"], "bead-B")
        self.assertEqual(chain[1]["distance"], 1)
    
    def test_down_chain_simple(self):
        """Test simple down chain."""
        mb = self.mem_beads
        # Code: source = newer, target = older
        # Add: A -> B -> C (A newest, C oldest)
        mb.add_edge("bead-B", "bead-C", "follows")  # B caused by C
        mb.add_edge("bead-A", "bead-B", "follows")  # A caused by B
        
        # Down chain from C should find B then A (effects of C)
        chain = mb.get_down_chain("bead-C")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["bead_id"], "bead-B")
        self.assertEqual(chain[0]["distance"], 1)
        self.assertEqual(chain[1]["bead_id"], "bead-A")
        self.assertEqual(chain[1]["distance"], 2)
    
    def test_up_chain_derives_from(self):
        """Test up chain with derives-from."""
        mb = self.mem_beads
        # Code: source = newer, target = older
        # "B derives-from A" means B (newer) built on A (older)
        mb.add_edge("bead-B", "bead-A", "derives-from")  # B newer, A older
        
        # Up chain from B should find A
        chain = mb.get_up_chain("bead-B")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["bead_id"], "bead-A")
    
    def test_down_chain_validates(self):
        """Test down chain with validates."""
        mb = self.mem_beads
        # Code: source = newer, target = older
        # "B validates A" means B (newer) validates A (older)
        mb.add_edge("bead-B", "bead-A", "validates")
        
        # Down chain from A should find B
        chain = mb.get_down_chain("bead-A")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["bead_id"], "bead-B")
    
    def test_context_chain(self):
        """Test context chain."""
        mb = self.mem_beads
        # Code: source = newer, target = older
        # B is context for A (B newer, A older)
        mb.add_edge("bead-B", "bead-A", "context")
        # A is recalled by C (A newer, C older)
        mb.add_edge("bead-A", "bead-C", "recalls")
        
        # Context chain includes both directions
        chain = mb.get_context_chain("bead-A")
        bead_ids = {c["bead_id"] for c in chain}
        self.assertEqual(len(bead_ids), 2)
        self.assertIn("bead-B", bead_ids)
        self.assertIn("bead-C", bead_ids)
    
    def test_max_depth(self):
        """Test max depth parameter."""
        mb = self.mem_beads
        # Code: source = newer (effect), target = older (cause)
        # D caused by C, C caused by B, B caused by A
        # Edges: D->C, C->B, B->A (source=effect, target=cause)
        mb.add_edge("bead-D", "bead-C", "follows")  # D caused by C
        mb.add_edge("bead-C", "bead-B", "follows")  # C caused by B
        mb.add_edge("bead-B", "bead-A", "follows")  # B caused by A
        
        # Up chain from D with max_depth=1 should only find C
        chain = mb.get_up_chain("bead-D", max_depth=1)
        self.assertEqual(len(chain), 1)


class TestBeadWithLinks(unittest.TestCase):
    """Test bead creation with links."""
    
    @classmethod
    def setUpClass(cls):
        """Create test environment."""
        cls.original_mem_beads_dir = os.environ.get("MEMBEADS_ROOT")
        os.makedirs(BEADS_DIR, exist_ok=True)
        os.environ["MEMBEADS_ROOT"] = BEADS_DIR
        
        # PATCH 10.A: Clear module cache so env var is re-read
        for mod in list(sys.modules.keys()):
            if 'mem_beads' in mod:
                del sys.modules[mod]
        
        import mem_beads
        cls.mem_beads = mem_beads
    
    @classmethod
    def tearDownClass(cls):
        """Cleanup."""
        if cls.original_mem_beads_dir:
            os.environ["MEMBEADS_ROOT"] = cls.original_mem_beads_dir
        else:
            os.environ.pop("MEMBEADS_ROOT", None)
        
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR)
    
    def setUp(self):
        """Clear edges before each test."""
        edges_path = os.path.join(BEADS_DIR, "edges.jsonl")
        if os.path.exists(edges_path):
            os.remove(edges_path)
    
    def test_bead_creation_creates_edges(self):
        """Test that creating a bead with links creates edges."""
        mb = self.mem_beads
        
        # Create first bead
        bead1 = mb.make_bead(
            bead_type="decision",
            title="Test decision",
            session_id="test"
        )
        mb.append_bead(bead1)
        
        # Create second bead with link - bead2 follows bead1
        # Code: source = newer bead, target = older bead
        # bead2 (newer) follows bead1 (older) -> edge: source=bead2, target=bead1
        bead2 = mb.make_bead(
            bead_type="outcome",
            title="Test outcome",
            session_id="test",
            links={"follows": [bead1["id"]]}
        )
        mb.append_bead(bead2)
        
        # Check edge was created: bead2 -> bead1
        edges = mb.get_edges_from(bead2["id"])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["target_id"], bead1["id"])
        self.assertEqual(edges[0]["type"], "follows")
    
    def test_bead_with_multiple_links(self):
        """Test bead with multiple link types."""
        mb = self.mem_beads
        
        bead1 = mb.make_bead(bead_type="decision", title="D1", session_id="test")
        bead2 = mb.make_bead(bead_type="evidence", title="E1", session_id="test")
        mb.append_bead(bead1)
        mb.append_bead(bead2)
        
        # bead3 (newest) derives-from bead1, context to bead2
        bead3 = mb.make_bead(
            bead_type="lesson",
            title="L1",
            session_id="test",
            links={
                "derives-from": [bead1["id"]],
                "context": [bead2["id"]]
            }
        )
        mb.append_bead(bead3)
        
        # Edges go from bead3 (newer) to older beads
        edges = mb.get_edges_from(bead3["id"])
        self.assertEqual(len(edges), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
