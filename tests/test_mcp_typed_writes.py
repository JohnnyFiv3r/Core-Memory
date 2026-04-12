import json
import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.mcp.typed_write import (
    MCP_TYPED_WRITE_TOOL_SCHEMAS,
    write_turn_finalized,
    apply_reviewed_proposal,
    submit_entity_merge_proposal,
)
from core_memory.runtime.dreamer_candidates import enqueue_dreamer_candidates, list_dreamer_candidates


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
                user_query="remember this",
                assistant_final="noted",
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("mcp.write_turn_finalized.v1", out.get("contract"))
            self.assertEqual("canonical_in_process", out.get("authority_path"))
            self.assertEqual(1, int(out.get("processed") or 0))

            events_file = Path(td) / ".beads" / "events" / "memory-events.jsonl"
            rows = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
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
            cid = str(((list_dreamer_candidates(root=td, status="pending", limit=5).get("results") or [{}])[0].get("id") or ""))
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

            rows = (list_dreamer_candidates(root=td, status="pending", limit=50).get("results") or [])
            hit = next((r for r in rows if str(r.get("id") or "") == cid), None)
            self.assertIsNotNone(hit)
            self.assertEqual("entity_merge_candidate", str((hit or {}).get("hypothesis_type") or ""))


if __name__ == "__main__":
    unittest.main()
