import tempfile
import unittest

from core_memory.integrations.mcp.typed_read import (
    MCP_TYPED_READ_TOOL_SCHEMAS,
    query_current_state,
    query_temporal_window,
    query_causal_chain,
    query_contradictions,
)
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.store_claim_ops import write_claims_to_bead, write_claim_updates_to_bead


class TestMCPTypedReads(unittest.TestCase):
    def _seed_claims(self, root: str):
        write_claims_to_bead(
            root,
            "bead1",
            [
                {
                    "id": "c1",
                    "claim_kind": "profile",
                    "subject": "user",
                    "slot": "timezone",
                    "value": "UTC",
                    "reason_text": "user stated",
                    "confidence": 0.9,
                }
            ],
        )

    def test_schema_registry_has_expected_tools(self):
        self.assertIn("query_current_state", MCP_TYPED_READ_TOOL_SCHEMAS)
        self.assertIn("query_temporal_window", MCP_TYPED_READ_TOOL_SCHEMAS)
        self.assertIn("query_causal_chain", MCP_TYPED_READ_TOOL_SCHEMAS)
        self.assertIn("query_contradictions", MCP_TYPED_READ_TOOL_SCHEMAS)

    def test_query_current_state_returns_claim_slot(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed_claims(td)
            out = query_current_state(root=td, slot_key="user:timezone", k=5)
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.query_current_state.v1", out.get("contract"))
            cur = dict(out.get("current_state") or {})
            self.assertIn(cur.get("status"), {"active", "conflict", "retracted", "not_found"})
            self.assertEqual("UTC", str((cur.get("current_claim") or {}).get("value") or ""))

    def test_query_temporal_window_contract(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="TZ changed", summary=["timezone moved to UTC"], session_id="main", source_turn_ids=["t1"])
            out = query_temporal_window(
                root=td,
                query="what was timezone",
                window_start="2026-01-01T00:00:00Z",
                window_end="2026-01-31T23:59:59Z",
                k=5,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.query_temporal_window.v1", out.get("contract"))
            self.assertIn("retrieval", out)

    def test_query_causal_chain_contract(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Rollout", summary=["changed rollout"], session_id="main", source_turn_ids=["t1"])
            out = query_causal_chain(root=td, query="why did rollout change", k=5)
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.query_causal_chain.v1", out.get("contract"))
            trace = dict(out.get("trace") or {})
            self.assertIn("anchors", trace)

    def test_query_contradictions_includes_claim_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(
                td,
                "bead1",
                [
                    {
                        "id": "c1",
                        "claim_kind": "preference",
                        "subject": "user",
                        "slot": "drink",
                        "value": "coffee",
                        "reason_text": "stated",
                        "confidence": 0.8,
                    }
                ],
            )
            write_claim_updates_to_bead(
                td,
                "bead2",
                [
                    {
                        "id": "u1",
                        "decision": "conflict",
                        "target_claim_id": "c1",
                        "subject": "user",
                        "slot": "drink",
                        "reason_text": "contradiction",
                        "trigger_bead_id": "bead2",
                    }
                ],
            )

            out = query_contradictions(root=td, slot_key="user:drink", k=5)
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.query_contradictions.v1", out.get("contract"))
            self.assertTrue(list(out.get("claim_conflicts") or []))


if __name__ == "__main__":
    unittest.main()
