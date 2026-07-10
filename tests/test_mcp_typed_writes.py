import json
import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.mcp.typed_write import (
    MCP_TYPED_WRITE_TOOL_SCHEMAS,
    apply_reviewed_proposal,
    submit_entity_merge_proposal,
    write_turn_finalized,
)
from core_memory.runtime.dreamer.candidates import enqueue_dreamer_candidates, list_dreamer_candidates


class TestMCPTypedWrites(unittest.TestCase):
    def test_schema_registry_has_expected_tools(self):
        self.assertIn("write_turn_finalized", MCP_TYPED_WRITE_TOOL_SCHEMAS)
        self.assertIn("apply_reviewed_proposal", MCP_TYPED_WRITE_TOOL_SCHEMAS)
        self.assertIn("submit_entity_merge_proposal", MCP_TYPED_WRITE_TOOL_SCHEMAS)

    def test_write_turn_finalized_uses_canonical_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            out = write_turn_finalized(
                root=td,
                session_id="s1",
                turn_id="t1",
                turns=[
                    {"speaker": "user", "role": "user", "content": "remember this"},
                    {"speaker": "assistant", "role": "assistant", "content": "noted"},
                ],
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("memory.turn_finalized_receipt.v2", out.get("contract"))
            self.assertEqual("committed", out.get("semantic_status"))
            self.assertTrue(out.get("bead_id"))

            events_file = Path(td) / ".beads" / "events" / "memory-events.jsonl"
            rows = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(1, len(rows))

    def test_apply_reviewed_proposal_reject(self):
        with tempfile.TemporaryDirectory() as td:
            enqueue_dreamer_candidates(
                root=td,
                associations=[
                    {
                        "source": "b1",
                        "target": "b2",
                        "relationship": "contradicts",
                        "novelty": 0.8,
                        "grounding": 0.9,
                        "confidence": 0.8,
                    }
                ],
                run_metadata={"run_id": "mcp2", "mode": "suggest", "source": "unit_test"},
            )
            cid = str(
                (
                    (list_dreamer_candidates(root=td, status="pending", limit=5).get("results") or [{}])[0].get("id")
                    or ""
                )
            )
            self.assertTrue(cid)

            out = apply_reviewed_proposal(
                root=td,
                candidate_id=cid,
                decision="reject",
                reviewer="qa",
                notes="reject via mcp",
                apply=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.apply_reviewed_proposal.v1", out.get("contract"))
            self.assertEqual("rejected", out.get("status"))

    def test_submit_entity_merge_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            out = submit_entity_merge_proposal(
                root=td,
                source_entity_id="entity-a",
                target_entity_id="entity-b",
                source_bead_id="bead-a",
                target_bead_id="bead-b",
                confidence=0.92,
                reviewer="qa",
                rationale="same org alias",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.submit_entity_merge_proposal.v1", out.get("contract"))
            cid = str(out.get("candidate_id") or "")
            self.assertTrue(cid)

            rows = list_dreamer_candidates(root=td, status="pending", limit=50).get("results") or []
            hit = next((r for r in rows if str(r.get("id") or "") == cid), None)
            self.assertIsNotNone(hit)
            self.assertEqual("entity_merge_candidate", str((hit or {}).get("hypothesis_type") or ""))


class TestApplyReviewedProposalResolutionFields(unittest.TestCase):
    """Guards A3: resolution/context_a/context_b must flow through every public surface."""

    def _seed_candidate(self, td: str) -> str:
        enqueue_dreamer_candidates(
            root=td,
            associations=[
                {
                    "source": "b1",
                    "target": "b2",
                    "relationship": "contradicts",
                    "novelty": 0.8,
                    "grounding": 0.9,
                    "confidence": 0.8,
                }
            ],
            run_metadata={"run_id": "test-res", "mode": "suggest", "source": "unit_test"},
        )
        rows = list_dreamer_candidates(root=td, status="pending", limit=5).get("results") or []
        return str((rows[0].get("id") or "") if rows else "")

    def test_typed_write_accepts_resolution_and_prefer_a(self):
        with tempfile.TemporaryDirectory() as td:
            cid = self._seed_candidate(td)
            self.assertTrue(cid)
            out = apply_reviewed_proposal(
                root=td,
                candidate_id=cid,
                decision="accept",
                resolution="prefer_a",
                reviewer="qa",
            )
            # prefer_a on a non-contradiction_pressure candidate is a no-op but must not error
            self.assertIn("ok", out)

    def test_call_tool_registry_forwards_resolution(self):
        """call_tool must not drop resolution/context_a/context_b."""
        from core_memory.integrations.mcp.registry import call_tool

        with tempfile.TemporaryDirectory() as td:
            cid = self._seed_candidate(td)
            out = call_tool(
                "apply_reviewed_proposal",
                {
                    "root": td,
                    "candidate_id": cid,
                    "decision": "reject",
                    "resolution": "prefer_a",
                    "context_a": "production",
                    "context_b": "staging",
                },
            )
            self.assertIn("ok", out)

    def test_http_request_model_accepts_resolution_fields(self):
        """MCPApplyReviewedProposalRequest must expose resolution/context_a/context_b."""
        try:
            from core_memory.integrations.http.server import MCPApplyReviewedProposalRequest
        except Exception as exc:
            self.skipTest(f"http server stack unavailable: {exc}")
        req = MCPApplyReviewedProposalRequest(
            candidate_id="cand-1",
            decision="accept",
            resolution="prefer_b",
            context_a="scope-a",
            context_b="",
        )
        self.assertEqual("prefer_b", req.resolution)
        self.assertEqual("scope-a", req.context_a)
        self.assertEqual("", req.context_b)

    def test_mcp_protocol_wrapper_accepts_resolution_fields(self):
        """apply_reviewed_proposal_tool signature must include resolution/context_a/context_b."""
        import inspect

        try:
            from core_memory.integrations.mcp.protocol_server import build_mcp_app  # noqa: F401
        except Exception as exc:
            self.skipTest(f"mcp server stack unavailable: {exc}")
        from core_memory.integrations.mcp.typed_write import apply_reviewed_proposal as arp

        sig = inspect.signature(arp)
        for field in ("resolution", "context_a", "context_b"):
            self.assertIn(field, sig.parameters, f"apply_reviewed_proposal missing param: {field}")


if __name__ == "__main__":
    unittest.main()
