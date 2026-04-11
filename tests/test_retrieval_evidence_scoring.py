import unittest

from core_memory.retrieval.evidence_scoring import rerank_semantic_rows


class TestRetrievalEvidenceScoring(unittest.TestCase):
    def test_fact_first_prefers_claim_anchor_softly(self):
        by_id = {
            "b1": {"bead": {"id": "b1", "title": "timezone", "summary": ["user timezone UTC"], "created_at": "2026-01-01T00:00:00Z"}},
            "b2": {"bead": {"id": "b2", "title": "random", "summary": ["unrelated"], "created_at": "2026-01-01T00:00:00Z"}},
        }
        rows = [
            {"bead_id": "b2", "score": 0.9, "anchor_reason": "retrieved", "context_bias_score": 0.0},
            {"bead_id": "b1", "score": 0.7, "anchor_reason": "claim_current_state", "context_bias_score": 0.0},
        ]
        out = rerank_semantic_rows(
            rows=rows,
            by_id=by_id,
            query="what is my timezone",
            retrieval_mode="fact_first",
            claim_state=None,
            as_of=None,
        )
        self.assertEqual("b1", out[0].get("bead_id"))
        self.assertGreater(float((out[0].get("feature_scores") or {}).get("claim_match") or 0.0), 0.9)

    def test_temporal_as_of_penalizes_out_of_interval(self):
        by_id = {
            "old": {
                "bead": {
                    "id": "old",
                    "title": "tz old",
                    "summary": ["UTC"],
                    "effective_from": "2026-01-01T00:00:00Z",
                    "effective_to": "2026-01-10T00:00:00Z",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            },
            "new": {
                "bead": {
                    "id": "new",
                    "title": "tz new",
                    "summary": ["America/Chicago"],
                    "effective_from": "2026-01-10T00:00:00Z",
                    "created_at": "2026-01-10T00:00:00Z",
                }
            },
        }
        rows = [
            {"bead_id": "old", "score": 0.8, "anchor_reason": "retrieved", "context_bias_score": 0.0},
            {"bead_id": "new", "score": 0.8, "anchor_reason": "retrieved", "context_bias_score": 0.0},
        ]
        out = rerank_semantic_rows(
            rows=rows,
            by_id=by_id,
            query="what was my timezone",
            retrieval_mode="temporal_first",
            claim_state=None,
            as_of="2026-01-05T00:00:00Z",
        )
        self.assertEqual("old", out[0].get("bead_id"))
        old_tf = float((out[0].get("feature_scores") or {}).get("temporal_fit") or 0.0)
        new_tf = float((out[1].get("feature_scores") or {}).get("temporal_fit") or 0.0)
        self.assertGreater(old_tf, new_tf)


if __name__ == "__main__":
    unittest.main()
