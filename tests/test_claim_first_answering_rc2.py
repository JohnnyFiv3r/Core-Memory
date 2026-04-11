import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claims_to_bead
from core_memory.retrieval.tools import memory as memory_tools


class TestClaimFirstAnsweringRC2(unittest.TestCase):
    def _run_claim_query(self, *, slot: str, value: str, claim_kind: str, query: str):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {
                "CORE_MEMORY_CLAIM_LAYER": "1",
                "CORE_MEMORY_CLAIM_RESOLUTION": "1",
                "CORE_MEMORY_CLAIM_RETRIEVAL_BOOST": "1",
                # explicit degraded mode path check
                "CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed",
            },
            clear=False,
        ):
            s = MemoryStore(td)
            s.add_bead(type="context", title="profile", summary=["profile context"], session_id="main", source_turn_ids=["t1"])
            write_claims_to_bead(
                td,
                "bead-claim",
                [
                    {
                        "id": f"claim-{slot}",
                        "claim_kind": claim_kind,
                        "subject": "user",
                        "slot": slot,
                        "value": value,
                        "reason_text": "user explicitly stated this slot",
                        "confidence": 0.91,
                    }
                ],
            )

            out = memory_tools.execute(
                {
                    "raw_query": query,
                    "intent": "remember",
                    "grounding_mode": "search_only",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            self.assertEqual("answer_current", out.get("answer_outcome"))
            first = (out.get("results") or [{}])[0]
            self.assertEqual("claim_state", first.get("source_surface"))
            self.assertEqual("claim_current_state", first.get("anchor_reason"))
            self.assertIn("answer_candidate", out)
            cand = dict(out.get("answer_candidate") or {})
            self.assertEqual("claim_state", cand.get("source"))
            self.assertEqual(f"user:{slot}", cand.get("slot_key"))
            self.assertIn(str(value), str(cand.get("text") or ""))
            self.assertTrue(any(str((c or {}).get("reason") or "") == "claim_state_current_slot" for c in (out.get("citations") or [])))

    def test_timezone_claim_first_current_answer(self):
        self._run_claim_query(
            slot="timezone",
            value="America/Chicago",
            claim_kind="condition",
            query="what is my timezone",
        )

    def test_preference_claim_first_current_answer(self):
        self._run_claim_query(
            slot="preference_coding",
            value="Neovim",
            claim_kind="preference",
            query="what do I prefer for coding",
        )

    def test_policy_claim_first_current_answer(self):
        self._run_claim_query(
            slot="response_format",
            value="bullet_lists",
            claim_kind="policy",
            query="what response format policy do I have",
        )


if __name__ == "__main__":
    unittest.main()
