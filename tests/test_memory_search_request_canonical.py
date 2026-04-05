import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_request, memory_search_typed
from core_memory.retrieval.tools import memory as memory_tools


class TestMemorySearchRequestCanonical(unittest.TestCase):
    def test_request_path_uses_request_normalization_not_typed_snaps(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="decision",
                title="Candidate-first promotion",
                summary=["promotion workflow"],
                tags=["promotion_workflow"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            out = memory_tools.search(
                request={"query": "candidate-first promotion", "intent": "remember", "k": 5},
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertNotIn("snapped_query", out)
            ex = out.get("explain") or {}
            self.assertIn("request_normalization", ex)
            self.assertNotIn("snapped_query", ex)

    def test_form_submission_alias_remains_compatible(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="decision",
                title="Policy",
                summary=["carry forward"],
                tags=["policy"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            out = memory_tools.search(
                form_submission={"query_text": "policy", "intent": "remember", "k": 5},
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertNotIn("snapped_query", out)

            typed = memory_search_typed(
                td,
                {"query_text": "policy", "intent": "remember", "k": 5},
                explain=True,
            )
            self.assertTrue(typed.get("ok"))
            self.assertIn("snapped_query", typed)
            self.assertIn("snapped_query", typed.get("explain") or {})

    def test_pipeline_request_accepts_facets_and_constraints(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="decision",
                title="Retention policy",
                summary=["workflow"],
                tags=["policy"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            out = memory_search_request(
                root=td,
                request={
                    "raw_query": "retention",
                    "intent": "remember",
                    "k": 5,
                    "facets": {"topic_keys": ["policy"]},
                    "constraints": {"require_structural": False},
                },
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            ex = out.get("explain") or {}
            rn = ex.get("request_normalization") or {}
            self.assertTrue(rn.get("facets_used"))
            self.assertTrue(rn.get("constraints_used"))


if __name__ == "__main__":
    unittest.main()
