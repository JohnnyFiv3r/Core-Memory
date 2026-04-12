import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claims_to_bead, write_claim_updates_to_bead
from core_memory.retrieval.tools import memory as memory_tools


class TestContradictionSupersessionRC3(unittest.TestCase):
    def test_current_stance_prefers_unsuperseded_candidate(self):
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
            old_id = s.add_bead(
                type="decision",
                title="Timezone stance old",
                summary=["Current timezone stance is UTC"],
                session_id="main",
                source_turn_ids=["t1"],
            )
            new_id = s.add_bead(
                type="decision",
                title="Timezone stance current",
                summary=["Current timezone stance is America Chicago"],
                session_id="main",
                source_turn_ids=["t2"],
            )
            s.link(new_id, old_id, "supersedes", explanation="new stance supersedes old", confidence=0.95)

            out = memory_tools.execute(
                {
                    "raw_query": "what is the current timezone stance",
                    "intent": "remember",
                    "grounding_mode": "search_only",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            results = list(out.get("results") or [])
            self.assertTrue(results)
            self.assertEqual(new_id, results[0].get("bead_id"))

            old_rows = [r for r in results if r.get("bead_id") == old_id]
            if old_rows:
                self.assertGreater(
                    float((old_rows[0].get("feature_scores") or {}).get("supersession_penalty") or 0.0),
                    0.0,
                )
                self.assertGreaterEqual(float(results[0].get("rank_score") or 0.0), float(old_rows[0].get("rank_score") or 0.0))

    def test_conflict_state_forces_partial_with_reason(self):
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
            s.add_bead(type="context", title="profile", summary=["profile"], session_id="main", source_turn_ids=["t1"])
            write_claims_to_bead(
                td,
                "bead-claims",
                [
                    {
                        "id": "c1",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "UTC",
                        "reason_text": "old",
                        "confidence": 0.8,
                    },
                    {
                        "id": "c2",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "America/Chicago",
                        "reason_text": "new conflicting",
                        "confidence": 0.8,
                    },
                ],
            )
            write_claim_updates_to_bead(
                td,
                "bead-claims",
                [
                    {
                        "id": "u1",
                        "decision": "conflict",
                        "target_claim_id": "c1",
                        "subject": "user",
                        "slot": "timezone",
                        "reason_text": "conflicting evidence",
                        "trigger_bead_id": "bead-claims",
                        "confidence": 0.7,
                    }
                ],
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
            self.assertEqual("answer_partial", out.get("answer_outcome"))
            policy = dict(out.get("answer_policy") or {})
            self.assertEqual("conflict_penalty_high", policy.get("decision_reason"))


if __name__ == "__main__":
    unittest.main()
