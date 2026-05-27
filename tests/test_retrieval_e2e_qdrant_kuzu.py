"""E2E retrieval tests with Qdrant embedded + Kuzu graph backends.

These tests exercise the full write→retrieve pipeline with real backends —
no mocks. Semantic embeddings use lexical fallback (FastEmbed model downloads
are skipped in CI); all assertions are designed to hold under lexical ranking.

Backend env: CORE_MEMORY_VECTOR_BACKEND=qdrant, CORE_MEMORY_GRAPH_BACKEND=kuzu,
CORE_MEMORY_CANONICAL_SEMANTIC_MODE=degraded_allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

def _retract_bead(root: str, bead_id: str) -> None:
    """Mark a bead as retracted in index.json and every session JSONL that contains it.

    build_visible_corpus reads both index.json and session JSONL files, with session
    JSONL taking precedence. We must update both so the retraction is visible.
    """
    import datetime
    root_p = Path(root)

    # Update index.json projection
    idx_path = root_p / ".beads" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    if bead_id in (idx.get("beads") or {}):
        idx["beads"][bead_id]["status"] = "retracted"
    idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")

    # Append a retraction record to any session JSONL that contains this bead,
    # so the session surface overlay also reflects the retraction.
    retraction_line = json.dumps({
        "id": bead_id,
        "status": "retracted",
        "retracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    beads_dir = root_p / ".beads"
    for session_file in beads_dir.glob("session-*.jsonl"):
        content = session_file.read_text(encoding="utf-8")
        if bead_id in content:
            with open(session_file, "a", encoding="utf-8") as f:
                f.write(retraction_line + "\n")

try:
    import qdrant_client  # noqa: F401
    import kuzu  # noqa: F401
    _BACKENDS_AVAILABLE = True
except ImportError:
    _BACKENDS_AVAILABLE = False

import core_memory.retrieval.semantic_index as _sem_idx
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.canonical import build_visible_corpus
from core_memory.retrieval.tools import memory as memory_tools


_BACKEND_ENV = {
    "CORE_MEMORY_VECTOR_BACKEND": "qdrant",
    "CORE_MEMORY_GRAPH_BACKEND": "kuzu",
    "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
    "CORE_MEMORY_SEMANTIC_BUILD_ON_READ": "1",
}


@unittest.skipUnless(_BACKENDS_AVAILABLE, "qdrant-client or kuzu not installed")
class TestRetrievalE2EQdrantKuzu(unittest.TestCase):
    """Full pipeline E2E: write beads → retrieve via Qdrant/Kuzu (lexical fallback)."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.root = cls._tmpdir.name

        _sem_idx._startup_check_done = False

        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            cls.store = MemoryStore(cls.root)

            # --- Session 1: decision + 3 caused_by children + retracted bead ---
            cls.decision_id = cls.store.add_bead(
                type="decision",
                title="Adopt Qdrant as default vector backend",
                summary=["Replaced FAISS with Qdrant for hybrid dense+sparse retrieval"],
                session_id="s1",
                source_turn_ids=["t1"],
            )
            cls.child1_id = cls.store.add_bead(
                type="outcome",
                title="FastEmbed provides sparse vectors",
                summary=["Qdrant FastEmbed integration enables vocabulary-based retrieval"],
                retrieval_facts=["FastEmbed", "Qdrant", "sparse vector", "BM42"],
                session_id="s1",
                source_turn_ids=["t2"],
            )
            cls.child2_id = cls.store.add_bead(
                type="outcome",
                title="Embedded Qdrant needs no server",
                summary=["QdrantClient path mode runs local with zero ops"],
                session_id="s1",
                source_turn_ids=["t3"],
            )
            cls.child3_id = cls.store.add_bead(
                type="outcome",
                title="UUID5 mapping solves ID constraint",
                summary=["Qdrant embedded requires UUID or integer point IDs"],
                session_id="s1",
                source_turn_ids=["t4"],
            )
            cls.retracted_id = cls.store.add_bead(
                type="context",
                title="Legacy FAISS approach considered",
                summary=["Initial plan was to keep FAISS as primary index"],
                session_id="s1",
                source_turn_ids=["t5"],
            )

            # Write causal associations for the decision bead directly (mirrors to Kuzu)
            cls.store.link(
                source_id=cls.decision_id,
                target_id=cls.child1_id,
                relationship="caused_by",
                explanation="decision led to FastEmbed outcome",
                confidence=0.9,
            )
            cls.store.link(
                source_id=cls.decision_id,
                target_id=cls.child2_id,
                relationship="caused_by",
                explanation="decision led to embedded mode outcome",
                confidence=0.9,
            )
            cls.store.link(
                source_id=cls.decision_id,
                target_id=cls.child3_id,
                relationship="caused_by",
                explanation="decision led to UUID5 outcome",
                confidence=0.9,
            )

            # --- Session 2: supersedes chain (3 beads) + cluster ---
            cls.old_config_id = cls.store.add_bead(
                type="context",
                title="Old configuration: FAISS with hash provider",
                summary=["Original retrieval used FAISS index with hash embeddings"],
                session_id="s2",
                source_turn_ids=["t6"],
            )
            cls.mid_config_id = cls.store.add_bead(
                type="context",
                title="Interim configuration: FAISS with OpenAI embeddings",
                summary=["Upgrade to OpenAI provider while keeping FAISS index"],
                session_id="s2",
                source_turn_ids=["t7"],
            )
            cls.new_config_id = cls.store.add_bead(
                type="context",
                title="New configuration: Qdrant hybrid search",
                summary=["Final configuration uses Qdrant for both dense and sparse retrieval"],
                session_id="s2",
                source_turn_ids=["t8"],
            )

            # Supersedes chain associations
            cls.store.link(
                source_id=cls.mid_config_id,
                target_id=cls.old_config_id,
                relationship="supersedes",
                confidence=0.95,
                explanation="FAISS+OpenAI supersedes hash-FAISS",
            )
            cls.store.link(
                source_id=cls.new_config_id,
                target_id=cls.mid_config_id,
                relationship="supersedes",
                confidence=0.95,
                explanation="Qdrant supersedes FAISS+OpenAI",
            )

            # Associated_with cluster (5 beads)
            cls.cluster_ids = []
            for i, topic in enumerate(["kuzu", "graph traversal", "causal chains", "BFS hops", "edge types"]):
                bid = cls.store.add_bead(
                    type="lesson",
                    title=f"Kuzu lesson: {topic}",
                    summary=[f"Implementation detail about {topic} in the graph backend"],
                    session_id="s2",
                    source_turn_ids=[f"t{9 + i}"],
                )
                cls.cluster_ids.append(bid)

            # Cross-link cluster members
            for i in range(len(cls.cluster_ids) - 1):
                cls.store.link(
                    source_id=cls.cluster_ids[i],
                    target_id=cls.cluster_ids[i + 1],
                    relationship="associated_with",
                    confidence=0.7,
                    explanation="related graph concepts",
                )

            # --- Session 3: proper-noun beads, cross-session, rolling-window ---
            cls.proper_noun_id = cls.store.add_bead(
                type="design_principle",
                title="Architecture principle for retrieval",
                summary=["Core architectural decision about retrieval stack"],
                # Proper noun only in retrieval_facts, NOT in title/summary
                retrieval_facts=["FastEmbed", "BM42", "ColBERT", "Qdrant hybrid query"],
                session_id="s3",
                source_turn_ids=["t14"],
            )
            cls.cross_session_id = cls.store.add_bead(
                type="lesson",
                title="Lesson about vector database selection",
                summary=["Key insight: embedded mode eliminates ops burden for agent memory"],
                session_id="s3",
                source_turn_ids=["t15"],
            )
            # Bead for rolling-window test (written last)
            cls.fresh_bead_id = cls.store.add_bead(
                type="context",
                title="Fresh bead for rolling window test",
                summary=["This bead was just written and should appear in corpus immediately"],
                session_id="s3",
                source_turn_ids=["t16"],
            )

        _sem_idx._startup_check_done = False

    @classmethod
    def tearDownClass(cls):
        try:
            cls._tmpdir.cleanup()
        except Exception:
            pass

    def _search(self, query: str, k: int = 8) -> dict:
        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            _sem_idx._startup_check_done = False
            return memory_tools.search(
                form_submission={"query_text": query, "intent": "remember", "k": k},
                root=self.root,
            )

    def _trace(self, query: str = "", anchor_ids: list[str] | None = None, k: int = 5) -> dict:
        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            _sem_idx._startup_check_done = False
            return memory_tools.trace(query=query, anchor_ids=anchor_ids, root=self.root, k=k)

    # ------------------------------------------------------------------
    # Test 1: proper-noun keyword in retrieval_facts surfaces the bead
    # ------------------------------------------------------------------
    def test_keyword_recall_proper_noun(self):
        """Bead with 'FastEmbed' only in retrieval_facts must appear in search results."""
        out = self._search("FastEmbed integration", k=10)
        self.assertTrue(out.get("ok") or out.get("degraded"), f"search failed: {out.get('error')}")
        bead_ids = [str((r or {}).get("bead_id") or "") for r in (out.get("results") or [])]
        self.assertIn(
            self.proper_noun_id,
            bead_ids,
            f"proper-noun bead not found in results: {bead_ids[:5]}",
        )

    # ------------------------------------------------------------------
    # Test 2: retracted beads excluded from results
    # ------------------------------------------------------------------
    def test_retracted_bead_excluded(self):
        """Retract a bead; subsequent search must not return it."""
        _retract_bead(self.root, self.retracted_id)

        out = self._search("FAISS legacy approach considered", k=10)
        self.assertTrue(out.get("ok") or out.get("degraded"), f"search failed: {out.get('error')}")
        bead_ids = [str((r or {}).get("bead_id") or "") for r in (out.get("results") or [])]
        self.assertNotIn(
            self.retracted_id,
            bead_ids,
            "retracted bead appeared in search results",
        )

    # ------------------------------------------------------------------
    # Test 3: causal chain from decision bead shows full grounding
    # ------------------------------------------------------------------
    def test_causal_chain_grounding_full(self):
        """Trace from decision bead via Kuzu; expect all 3 caused_by children in chains."""
        out = self._trace(anchor_ids=[self.decision_id], k=5)
        self.assertTrue(out.get("ok"), f"trace failed: {out.get('error')}")

        # Collect all bead IDs mentioned across chains
        chain_bead_ids: set[str] = set()
        for chain in out.get("chains") or []:
            for node in chain.get("beads") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))
            for node in chain.get("nodes") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))

        # All 3 caused_by children must appear in the chains
        child_ids = {self.child1_id, self.child2_id, self.child3_id}
        self.assertTrue(
            child_ids.issubset(chain_bead_ids),
            f"not all caused_by children found in trace chains. missing={child_ids - chain_bead_ids}, chains={out.get('chains')[:2]}",
        )

    # ------------------------------------------------------------------
    # Test 4: supersedes chain is traversable in Kuzu
    # ------------------------------------------------------------------
    def test_supersession_chain_traversable(self):
        """Trace from the newest config bead; supersedes chain reachable via Kuzu."""
        out = self._trace(anchor_ids=[self.new_config_id], k=5)
        self.assertTrue(out.get("ok"), f"trace failed: {out.get('error')}")

        chain_bead_ids: set[str] = set()
        for chain in out.get("chains") or []:
            for node in chain.get("beads") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))
            for node in chain.get("nodes") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))

        # At least mid_config should be reachable (1 hop away via supersedes)
        self.assertTrue(
            chain_bead_ids.intersection({self.mid_config_id, self.old_config_id}),
            f"supersedes chain not traversable. chain_ids={chain_bead_ids}",
        )

    # ------------------------------------------------------------------
    # Test 5: cross-session recall — session 1 bead surfaces in s3 query
    # ------------------------------------------------------------------
    def test_cross_session_recall(self):
        """Bead from session 1 surfaces when its content matches a session 3 query."""
        # The decision bead has unique content about Qdrant as default vector backend
        out = self._search("Qdrant default vector backend adoption decision", k=10)
        self.assertTrue(out.get("ok") or out.get("degraded"), f"search failed: {out.get('error')}")
        bead_ids = [str((r or {}).get("bead_id") or "") for r in (out.get("results") or [])]
        self.assertIn(
            self.decision_id,
            bead_ids,
            f"cross-session recall failed; decision_id not found in {bead_ids[:5]}",
        )

    # ------------------------------------------------------------------
    # Test 6: associated_with cluster is traversable
    # ------------------------------------------------------------------
    def test_cluster_associated_with_traversable(self):
        """Trace from first cluster bead; associated_with chain reaches other members."""
        seed = self.cluster_ids[0]
        out = self._trace(anchor_ids=[seed], k=5)
        self.assertTrue(out.get("ok"), f"trace failed: {out.get('error')}")

        chain_bead_ids: set[str] = set()
        for chain in out.get("chains") or []:
            for node in chain.get("beads") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))
            for node in chain.get("nodes") or []:
                chain_bead_ids.add(str((node or {}).get("id") or ""))

        # Expect at least the second cluster member to be reachable (1 hop)
        self.assertTrue(
            chain_bead_ids.intersection(set(self.cluster_ids[1:])),
            f"cluster associated_with chain not traversable. chain_ids={chain_bead_ids}",
        )

    # ------------------------------------------------------------------
    # Test 7: rolling window — fresh bead in corpus before index rebuild
    # ------------------------------------------------------------------
    def test_rolling_window_independent_of_index(self):
        """build_visible_corpus includes a just-written bead without index rebuild."""
        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            corpus = build_visible_corpus(Path(self.root))
        corpus_ids = {str((b or {}).get("bead_id") or b.get("id") or "") for b in corpus}
        self.assertIn(
            self.fresh_bead_id,
            corpus_ids,
            f"fresh bead missing from corpus. corpus size={len(corpus_ids)}",
        )

    # ------------------------------------------------------------------
    # Test 8: migrate CLI is idempotent
    # ------------------------------------------------------------------
    def test_migrate_idempotent(self):
        """Run migrate twice; Kuzu node count must be identical after each run."""
        from core_memory.cli.handlers.migrate import handle_migrate

        args1 = argparse.Namespace(root=self.root, dry_run=False, skip_vectors=True, skip_graph=False)
        args2 = argparse.Namespace(root=self.root, dry_run=False, skip_vectors=True, skip_graph=False)

        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            rc1 = handle_migrate(args1)
            rc2 = handle_migrate(args2)

        self.assertEqual(rc1, 0, "first migrate run returned non-zero exit code")
        self.assertEqual(rc2, 0, "second migrate run returned non-zero exit code")

        # Verify Kuzu node count is stable across runs
        with patch.dict(os.environ, _BACKEND_ENV, clear=False):
            from core_memory.persistence.graph.factory import create_graph_backend
            graph = create_graph_backend(Path(self.root))

            # Write a known bead, re-migrate, count should not grow
            count_q = "MATCH (b:Bead) RETURN count(b) AS n"
            with graph._conn.execute(count_q) as res:
                count1 = res.get_next()[0]

            args3 = argparse.Namespace(root=self.root, dry_run=False, skip_vectors=True, skip_graph=False)
            handle_migrate(args3)

            with graph._conn.execute(count_q) as res:
                count2 = res.get_next()[0]

            graph.close()

        self.assertEqual(count1, count2, f"Kuzu node count changed on re-migrate: {count1} → {count2}")


if __name__ == "__main__":
    unittest.main()
