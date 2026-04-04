import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence.rolling_record_store import write_rolling_records
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_session_start
from core_memory.retrieval.tools import memory as memory_tools


class TestSessionStartRetrievalSemantics(unittest.TestCase):
    def test_session_start_is_searchable_but_demoted_for_generic_query(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False
        ):
            root = Path(td) / "memory"
            s = MemoryStore(str(root))
            s.add_bead(
                type="decision",
                title="Promotion policy",
                summary=["promotion policy decision"],
                detail="Use candidate-first promotion workflow",
                session_id="s1",
                source_turn_ids=["t1"],
            )

            write_rolling_records(
                str(root),
                records=[{"type": "decision", "title": "Promotion policy", "summary": ["carry forward"]}],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )
            out = process_session_start(root=str(root), session_id="s1", source="test")
            self.assertTrue(out.get("ok"))

            sr = memory_tools.search(
                request={"query_text": "promotion policy", "intent": "remember", "k": 8},
                root=str(root),
                explain=True,
            )
            self.assertTrue(sr.get("ok"))
            rows = sr.get("results") or []
            self.assertTrue(rows)
            self.assertEqual("decision", str((rows[0] or {}).get("type") or ""))

    def test_session_start_can_appear_for_continuity_queries(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False
        ):
            root = Path(td) / "memory"
            MemoryStore(str(root))
            process_session_start(root=str(root), session_id="s1", source="test")

            sr = memory_tools.search(
                request={"query_text": "session start continuity", "intent": "remember", "k": 8},
                root=str(root),
                explain=True,
            )
            self.assertTrue(sr.get("ok"))
            types = [str(r.get("type") or "") for r in (sr.get("results") or [])]
            self.assertIn("session_start", types)

    def test_session_start_alone_does_not_ground_full(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False
        ):
            root = Path(td) / "memory"
            MemoryStore(str(root))
            out = process_session_start(root=str(root), session_id="s1", source="test")
            bid = str(out.get("bead_id") or "")
            self.assertTrue(bid)

            tr = memory_tools.trace(query="", anchor_ids=[bid], root=str(root), k=5)
            self.assertTrue(tr.get("ok"))
            g = tr.get("grounding") or {}
            self.assertNotEqual("full", g.get("level"))


if __name__ == "__main__":
    unittest.main()
