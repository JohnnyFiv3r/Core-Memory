import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools import memory as memory_tools


class TestSemanticRequiredModeContract(unittest.TestCase):
    def test_search_required_mode_fails_closed_when_semantic_unavailable(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.search(
                form_submission={"query_text": "A", "intent": "remember", "k": 5},
                root=td,
                explain=True,
            )
            self.assertFalse(out.get("ok"))
            self.assertFalse(out.get("degraded", False))
            self.assertEqual("semantic_backend_unavailable", ((out.get("error") or {}).get("code") or ""))

    def test_trace_query_required_mode_fails_closed(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.trace(query="why A", root=td, k=5)
            self.assertFalse(out.get("ok"))
            self.assertEqual("semantic_backend_unavailable", ((out.get("error") or {}).get("code") or ""))

    def test_execute_required_mode_fails_closed(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.execute(
                request={"raw_query": "why A", "intent": "causal", "k": 5},
                root=td,
                explain=True,
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual("semantic_backend_unavailable", ((out.get("error") or {}).get("code") or ""))

    def test_trace_anchor_ids_bypasses_semantic_required_lookup(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "required",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            bid = s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.trace(query="", anchor_ids=[bid], root=td, k=5)
            self.assertTrue(out.get("ok"))
            ids = [a.get("bead_id") for a in (out.get("anchors") or [])]
            self.assertIn(bid, ids)

    def test_degraded_allowed_mode_returns_explicit_degraded_marker(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
                "CORE_MEMORY_EMBEDDINGS_PROVIDER": "openai",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_tools.search(
                form_submission={"query_text": "A", "intent": "remember", "k": 5},
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("degraded"))
            self.assertIn("semantic_backend_unavailable_degraded", out.get("warnings") or [])


if __name__ == "__main__":
    unittest.main()
