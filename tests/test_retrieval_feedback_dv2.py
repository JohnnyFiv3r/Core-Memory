import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates, list_dreamer_candidates
from core_memory.runtime.retrieval_feedback import record_retrieval_feedback, summarize_retrieval_feedback


class TestRetrievalFeedbackDV2(unittest.TestCase):
    def test_memory_execute_records_feedback_event(self):
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
            bid = s.add_bead(
                type="context",
                title="Timezone note",
                summary=["My timezone is America Chicago"],
                session_id="main",
                source_turn_ids=["t1"],
            )

            out = memory_tools.execute(
                {
                    "raw_query": "what is my timezone",
                    "intent": "remember",
                    "grounding_mode": "search_only",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            fb = dict(out.get("retrieval_feedback") or {})
            self.assertTrue(fb.get("recorded"))

            summary = summarize_retrieval_feedback(td, since="30d", limit=50)
            self.assertGreaterEqual(int((summary.get("counts") or {}).get("events") or 0), 1)
            beads = list(summary.get("top_beads") or [])
            self.assertTrue(any(str(r.get("bead_id") or "") == bid for r in beads))

    def test_dreamer_enqueue_consumes_retrieval_feedback_signals(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            tgt = s.add_bead(type="evidence", title="B", summary=["b"], session_id="main", source_turn_ids=["t1"])

            # deterministic feedback seed for the same source/target edge.
            rec = record_retrieval_feedback(
                td,
                request={"raw_query": "why decision a", "intent": "causal", "k": 5},
                response={
                    "ok": True,
                    "answer_outcome": "answer_partial",
                    "results": [
                        {"bead_id": src, "score": 0.9, "source_surface": "projection", "anchor_reason": "retrieved"},
                        {"bead_id": tgt, "score": 0.8, "source_surface": "projection", "anchor_reason": "retrieved"},
                    ],
                    "chains": [{"edges": [{"src": src, "dst": tgt, "rel": "supports"}]}],
                    "warnings": [],
                },
            )
            self.assertTrue(rec.get("ok"))

            out = enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": src,
                        "target": tgt,
                        "relationship": "supports",
                        "novelty": 0.7,
                        "grounding": 0.9,
                        "confidence": 0.9,
                    }
                ],
                run_metadata={"run_id": "dv2-feedback", "mode": "suggest", "source": "unit_test", "feedback_since": "30d"},
            )
            self.assertTrue(out.get("ok"))

            rows = list((list_dreamer_candidates(root=td, status="pending", limit=20).get("results") or []))
            self.assertTrue(rows)
            row = next(r for r in rows if str(r.get("source_bead_id") or "") == src and str(r.get("target_bead_id") or "") == tgt)
            fb = dict(row.get("retrieval_feedback") or {})
            self.assertGreaterEqual(int(fb.get("source_bead_hits") or 0), 1)
            self.assertGreaterEqual(int(fb.get("target_bead_hits") or 0), 1)
            self.assertGreaterEqual(int(fb.get("edge_hits") or 0), 1)


if __name__ == "__main__":
    unittest.main()
