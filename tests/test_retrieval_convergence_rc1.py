import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.search import search_typed
from core_memory.retrieval.tools import memory as memory_tools


class TestRetrievalConvergenceRC1(unittest.TestCase):
    def test_canonical_reports_semantic_and_hybrid_stage_counts(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Switch orchestration", summary=["Move to openclaw adapter first"], session_id="main", source_turn_ids=["t1"], tags=["migration"])
            s.add_bead(type="evidence", title="Error traces", summary=["legacy adapter mismatch"], session_id="main", source_turn_ids=["t1"], tags=["migration"])

            out = memory_tools.execute(
                {
                    "raw_query": "why did we switch orchestration adapters",
                    "intent": "remember",
                    "grounding_mode": "search_only",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            stages = dict(out.get("retrieval_stages") or {})
            self.assertGreaterEqual(int(stages.get("hybrid_seed_count") or 0), 1)
            self.assertGreaterEqual(int(stages.get("hybrid_rerank_count") or 0), 1)

    def test_search_typed_uses_shared_convergence_stages(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="context", title="Acme profile", summary=["Acme based in Chicago"], session_id="main", source_turn_ids=["t1"], entities=["Acme Corporation"])

            out = search_typed(
                s.root,
                {
                    "query_text": "where is acme corp based",
                    "intent": "remember",
                    "k": 5,
                    "require_structural": False,
                },
                include_explain=True,
            )

            self.assertTrue(out.get("ok"))
            stages = dict(out.get("retrieval_stages") or {})
            self.assertGreaterEqual(int(stages.get("hybrid_candidates") or 0), 1)
            self.assertGreaterEqual(int(stages.get("rerank_candidates") or 0), 1)


if __name__ == "__main__":
    unittest.main()
