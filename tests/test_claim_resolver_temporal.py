import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claims_to_bead
from core_memory.retrieval.tools import memory as memory_tools


class TestClaimResolverTemporal(unittest.TestCase):
    def test_resolve_as_of_selects_interval_valid_claim(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(
                td,
                "b1",
                [
                    {
                        "id": "c_old",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "UTC",
                        "reason_text": "old",
                        "confidence": 0.8,
                        "effective_from": "2026-01-01T00:00:00Z",
                        "effective_to": "2026-01-10T00:00:00Z",
                    }
                ],
            )
            write_claims_to_bead(
                td,
                "b2",
                [
                    {
                        "id": "c_new",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "America/Chicago",
                        "reason_text": "new",
                        "confidence": 0.9,
                        "effective_from": "2026-01-10T00:00:00Z",
                    }
                ],
            )

            early = resolve_all_current_state(td, as_of="2026-01-05T00:00:00Z")
            late = resolve_all_current_state(td, as_of="2026-01-12T00:00:00Z")

            self.assertEqual("UTC", ((early.get("slots") or {}).get("user:timezone") or {}).get("current_claim", {}).get("value"))
            self.assertEqual(
                "America/Chicago",
                ((late.get("slots") or {}).get("user:timezone") or {}).get("current_claim", {}).get("value"),
            )

    def test_retrieval_claim_state_honors_as_of(self):
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
            s.add_bead(type="context", title="tz timeline", summary=["timeline"], session_id="main", source_turn_ids=["t1"])
            write_claims_to_bead(
                td,
                "b1",
                [
                    {
                        "id": "c_old",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "UTC",
                        "reason_text": "old",
                        "confidence": 0.82,
                        "effective_from": "2026-01-01T00:00:00Z",
                        "effective_to": "2026-01-10T00:00:00Z",
                    },
                    {
                        "id": "c_new",
                        "claim_kind": "condition",
                        "subject": "user",
                        "slot": "timezone",
                        "value": "America/Chicago",
                        "reason_text": "new",
                        "confidence": 0.9,
                        "effective_from": "2026-01-10T00:00:00Z",
                    },
                ],
            )

            out = memory_tools.execute(
                {
                    "raw_query": "what is my timezone",
                    "intent": "remember",
                    "as_of": "2026-01-05T00:00:00Z",
                    "constraints": {"require_structural": False},
                    "k": 5,
                },
                root=td,
                explain=True,
            )

            self.assertTrue(out.get("ok"))
            self.assertEqual("2026-01-05T00:00:00Z", ((out.get("claim_context") or {}).get("as_of") or ""))
            first = (out.get("results") or [{}])[0]
            self.assertEqual("claim_state", first.get("source_surface"))
            self.assertIn("UTC", str(first.get("snippet") or ""))


if __name__ == "__main__":
    unittest.main()
