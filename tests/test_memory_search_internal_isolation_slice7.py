import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval import pipeline as retrieval_pipeline
from core_memory.retrieval.tools import memory as memory_tools


class TestMemorySearchInternalIsolationSlice7(unittest.TestCase):
    def test_request_first_path_does_not_call_typed_snap_layer(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="decision",
                title="Request-first retrieval",
                summary=["canonical request path"],
                tags=["request_first"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            with patch.object(
                retrieval_pipeline,
                "_snap_typed_submission",
                side_effect=AssertionError("typed snap layer should not run for request-first search"),
            ):
                out = memory_tools.search(
                    request={"query_text": "request-first", "intent": "remember", "k": 5},
                    root=td,
                    explain=True,
                )

            self.assertTrue(out.get("ok"))

    def test_typed_compat_path_uses_typed_snap_layer(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"},
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(
                type="decision",
                title="Typed compatibility",
                summary=["legacy typed shim"],
                tags=["typed_shim"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            snapped = {
                "query_text": "typed compatibility",
                "intent": "remember",
                "k": 5,
                "topic_keys": ["typed_shim"],
            }
            decisions = [{"rule": "stub", "reason": "test"}]

            with patch.object(retrieval_pipeline, "_snap_typed_submission", return_value=(snapped, decisions)) as spy:
                out = retrieval_pipeline.memory_search_typed(
                    root=td,
                    submission={"query_text": "ignored by stub", "k": 1},
                    explain=True,
                )

            self.assertEqual(1, spy.call_count)
            self.assertTrue(out.get("ok"))
            self.assertEqual(snapped, out.get("snapped_query"))
            self.assertEqual(decisions, (out.get("explain") or {}).get("snap_decisions"))


if __name__ == "__main__":
    unittest.main()
