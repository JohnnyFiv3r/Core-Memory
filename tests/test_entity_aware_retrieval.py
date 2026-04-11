import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline.search import search_typed
from core_memory.retrieval.tools import memory as memory_tools


class TestEntityAwareRetrieval(unittest.TestCase):
    def test_canonical_retrieval_resolves_entity_alias_and_boosts_match(self):
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
            acme_id = s.add_bead(
                type="context",
                title="Acme HQ",
                summary=["Acme Corporation is based in Chicago"],
                entities=["Acme Corporation"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            s.add_bead(
                type="context",
                title="Weather note",
                summary=["Chicago weather forecast"],
                session_id="main",
                source_turn_ids=["t2"],
            )

            out = memory_tools.execute(
                {
                    "raw_query": "where is acme corp based",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            ectx = dict(out.get("entity_context") or {})
            self.assertTrue(ectx.get("resolved_entity_ids"))

            first = (out.get("results") or [{}])[0]
            self.assertEqual(acme_id, first.get("bead_id"))
            fs = dict(first.get("feature_scores") or {})
            self.assertGreater(float(fs.get("entity_match") or 0.0), 0.5)

    def test_search_typed_includes_entity_context_from_alias(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="context",
                title="OpenAI office",
                summary=["OpenAI is in San Francisco"],
                entities=["OpenAI"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            out = search_typed(
                s.root,
                {
                    "query_text": "where is open ai located",
                    "intent": "remember",
                    "k": 5,
                    "require_structural": False,
                },
                include_explain=True,
            )

            self.assertTrue(out.get("ok"))
            ectx = dict(out.get("entity_context") or {})
            self.assertTrue(ectx.get("resolved_entity_ids"))


if __name__ == "__main__":
    unittest.main()
