import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claims_to_bead
from core_memory.retrieval.tools import memory as memory_tools


class TestClaimRetrievalCanonicalIntegration(unittest.TestCase):
    def test_canonical_search_planner_receives_claim_state(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Timezone preference", summary=["User timezone setup"], session_id="main", source_turn_ids=["t1"])
            write_claims_to_bead(
                td,
                "bead-claim",
                [
                    {
                        "id": "c1",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "UTC",
                        "reason_text": "user stated timezone",
                        "confidence": 0.9,
                    }
                ],
            )

            seen = {"catalog": None, "state": None}

            def _spy(query, catalog, current_state):
                seen["catalog"] = catalog
                seen["state"] = current_state
                return "fact_first"

            with patch("core_memory.retrieval.pipeline.canonical.plan_retrieval_mode", side_effect=_spy):
                out = memory_tools.execute(
                    {
                        "raw_query": "timezone",
                        "intent": "remember",
                        "constraints": {"require_structural": False},
                        "k": 5,
                    },
                    root=td,
                    explain=True,
                )

            self.assertTrue(out.get("ok"))
            self.assertEqual("fact_first", out.get("retrieval_mode"))
            self.assertIsInstance(seen["catalog"], dict)
            self.assertIsInstance(seen["state"], dict)
            self.assertGreaterEqual(int((seen["state"] or {}).get("total_slots") or 0), 1)

    def test_execute_exposes_answer_policy_when_claim_layer_enabled(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Coffee preference", summary=["User prefers coffee"], session_id="main", source_turn_ids=["t1"])

            out = memory_tools.execute(
                {
                    "raw_query": "what do I prefer",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            self.assertIn("answer_policy", out)
            self.assertIn("answer_outcome", out)
            self.assertIn(out.get("answer_outcome"), {"answer_current", "answer_historical", "answer_partial", "abstain"})

    def test_fact_query_prioritizes_claim_state_anchor_and_current_answer(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="context", title="Profile", summary=["user profile"], session_id="main", source_turn_ids=["t1"])
            write_claims_to_bead(
                td,
                "bead-claim-tz",
                [
                    {
                        "id": "claim-tz-1",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "America/Chicago",
                        "reason_text": "user explicitly stated timezone",
                        "confidence": 0.92,
                    }
                ],
            )

            out = memory_tools.execute(
                {
                    "raw_query": "what is my timezone",
                    "intent": "remember",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            self.assertEqual("fact_first", out.get("retrieval_mode"))
            first = (out.get("results") or [{}])[0]
            self.assertEqual("claim_state", first.get("source_surface"))
            self.assertEqual("claim_current_state", first.get("anchor_reason"))
            self.assertEqual("answer_current", out.get("answer_outcome"))


if __name__ == "__main__":
    unittest.main()
