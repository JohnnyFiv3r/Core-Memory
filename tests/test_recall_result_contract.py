import unittest

import core_memory
from core_memory.retrieval.contracts import (
    ClaimSlotItem,
    EvidenceItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    ResolvedGoalItem,
    SourceItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)


class TestRecallResultContract(unittest.TestCase):
    def test_public_exports(self):
        self.assertIs(core_memory.RecallResult, RecallResult)
        self.assertIs(core_memory.ClaimSlotItem, ClaimSlotItem)
        self.assertIs(core_memory.EvidenceItem, EvidenceItem)
        self.assertIs(core_memory.ResolvedGoalItem, ResolvedGoalItem)
        self.assertIs(core_memory.SourceItem, SourceItem)
        self.assertIs(core_memory.RecallStep, RecallStep)
        self.assertIs(core_memory.RecallPlanning, RecallPlanning)
        self.assertIs(core_memory.recall_result_from_memory_execute, recall_result_from_memory_execute)

    def test_round_trip_contract_shape(self):
        result = RecallResult(
            answer="Use pgvector.",
            why="It is the selected backend.",
            evidence=[EvidenceItem(bead_id="b1", type="decision", title="Pick pgvector", score=0.91, grounding_hash="sha256:e")],
            resolved_goals=[ResolvedGoalItem(bead_id="g1", title="Ship migration", resolved_by_bead_id="b1", resolved_at="2026-05-15T00:00:00Z", reason="matched outcome")],
            claim_slots={
                "Postgres:backend": ClaimSlotItem(
                    key="Postgres:backend",
                    subject="Postgres",
                    slot="backend",
                    current_value="pgvector",
                    status="active",
                    current_claim_id="c1",
                    chain_seq=3,
                    grounding_hash="sha256:c",
                )
            },
            sources=[SourceItem(turn_id="t1", session_id="s1", speaker="assistant", bead_id="b1")],
            tier_path=["semantic", "source"],
            steps=[RecallStep(tier="semantic", query="vector backend", status="ok", result_count=1)],
            planning=RecallPlanning(
                selected_effort="medium",
                reason="default user-facing recall",
                expected_shape={"bead_types": ["decision"]},
            ),
            status="answered",
            warnings=["demo"],
            metadata={"surface": "test"},
            raw={"ok": True},
        )

        data = result.to_dict()
        self.assertEqual("recall_result.v1", data["schema_version"])
        self.assertEqual("recall_result", data["contract"])
        self.assertEqual("b1", data["evidence"][0]["bead_id"])
        self.assertEqual("sha256:e", data["evidence"][0]["grounding_hash"])
        self.assertEqual("g1", data["resolved_goals"][0]["bead_id"])
        self.assertEqual("pgvector", data["claim_slots"]["Postgres:backend"]["current_value"])
        self.assertEqual(["decision"], data["planning"]["expected_shape"]["bead_types"])
        self.assertEqual("test", data["metadata"]["surface"])

        hydrated = RecallResult.from_dict(data)
        self.assertEqual(result.to_dict(), hydrated.to_dict())

    def test_effort_validation_uses_public_low_medium_high_dynamic_names(self):
        self.assertEqual("low", validate_recall_effort("LOW"))
        self.assertEqual("medium", validate_recall_effort("medium"))
        self.assertEqual("high", validate_recall_effort(" high "))
        self.assertEqual("dynamic", validate_recall_effort("dynamic"))
        with self.assertRaisesRegex(ValueError, "low"):
            validate_recall_effort("cheap")

    def test_empty_legacy_result_normalizes_to_empty_recall_result(self):
        result = recall_result_from_memory_execute({"ok": True, "results": []}, query="missing", include_raw=False)

        self.assertEqual("empty", result.status)
        self.assertIsNone(result.answer)
        self.assertEqual([], result.evidence)
        self.assertEqual(["semantic"], result.tier_path)
        self.assertEqual("medium", result.planning.selected_effort)
        self.assertIsNone(result.raw)
        self.assertEqual("missing", result.metadata["query"])
        self.assertEqual("memory_execute", result.metadata["source_surface"])
        self.assertEqual("missing", result.steps[0].query)

    def test_flat_search_result_normalizes_evidence_and_sources(self):
        raw = {
            "ok": True,
            "results": [
                {
                    "bead_id": "b1",
                    "type": "decision",
                    "title": "Use pgvector",
                    "supporting_facts": ["pgvector was chosen for deploy parity"],
                    "semantic_score": 0.83,
                    "anchor_reason": "retrieved",
                    "source_turn_ids": ["t1", "t2"],
                    "session_id": "s1",
                    "created_at": "2026-05-12T00:00:00Z",
                }
            ],
        }

        result = recall_result_from_memory_execute(raw, query="what vector backend", effort="low")

        self.assertEqual("partial", result.status)
        self.assertEqual(["semantic", "source"], result.tier_path)
        self.assertEqual("low", result.planning.selected_effort)
        self.assertEqual("b1", result.evidence[0].bead_id)
        self.assertEqual("decision", result.evidence[0].type)
        self.assertIn("pgvector", result.evidence[0].content_excerpt)
        self.assertEqual(0.83, result.evidence[0].score)
        self.assertEqual(["t1", "t2"], [s.turn_id for s in result.sources])

    def test_causal_result_normalizes_tier_path_steps_and_answer_candidate(self):
        raw = {
            "ok": True,
            "results": [{"bead_id": "b1", "title": "Timeout fix", "summary": ["Redis timeouts caused retries"]}],
            "chains": [{"edges": [{"src": "b1", "dst": "b2", "rel": "caused_by"}]}],
            "answer_candidate": {"answer": "Retries caused the timeout cascade.", "why": "Current claim slot matched."},
            "answer_outcome": "answer_current",
        }

        result = recall_result_from_memory_execute(raw, query="why timeouts", effort="high")

        self.assertEqual("answered", result.status)
        self.assertEqual("Retries caused the timeout cascade.", result.answer)
        self.assertEqual("Current claim slot matched.", result.why)
        self.assertEqual(["semantic", "causal"], result.tier_path)
        self.assertEqual("semantic", result.steps[0].tier)
        self.assertEqual("causal", result.steps[1].tier)
        self.assertEqual(1, result.steps[1].result_count)
        self.assertEqual("high", result.planning.selected_effort)

    def test_failed_legacy_result_preserves_warnings_and_status(self):
        result = recall_result_from_memory_execute(
            {"ok": False, "error": "bad", "warnings": ["w1"]},
            query="redis",
        )

        self.assertEqual("failed", result.status)
        self.assertEqual(["w1"], result.warnings)
        self.assertEqual("failed", result.steps[0].status)


if __name__ == "__main__":
    unittest.main()
