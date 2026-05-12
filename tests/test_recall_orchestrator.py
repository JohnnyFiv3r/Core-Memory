import unittest
from unittest.mock import patch

from core_memory.retrieval.agent import recall
from core_memory.retrieval.contracts import RecallResult


class TestRecallOrchestrator(unittest.TestCase):
    def test_low_effort_uses_search_only_and_returns_contract(self):
        raw = {
            "ok": True,
            "results": [
                {
                    "bead_id": "b1",
                    "title": "Redis timeout",
                    "type": "decision",
                    "retrieval_facts": ["We kept Redis timeouts low."],
                    "source_turn_ids": ["t1"],
                    "session_id": "s1",
                }
            ],
        }
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw) as spy:
            result = recall("redis timeout", effort="low", root="/tmp/memory", include_raw=False)

        self.assertIsInstance(result, RecallResult)
        self.assertEqual("partial", result.status)
        self.assertEqual("low", result.planning.selected_effort)
        self.assertEqual(["semantic", "source"], result.tier_path)
        self.assertEqual("b1", result.evidence[0].bead_id)
        self.assertIsNone(result.raw)
        request = spy.call_args.kwargs["request"]
        self.assertEqual("search_only", request["grounding_mode"])
        self.assertEqual(8, request["k"])
        self.assertNotIn("hydration", request)

    def test_medium_effort_uses_grounded_hydrated_defaults(self):
        with patch("core_memory.retrieval.agent.memory_execute", return_value={"ok": True, "results": []}) as spy:
            result = recall("what did we decide about pgvector?", effort="medium")

        self.assertEqual("medium", result.planning.selected_effort)
        self.assertIn("decision", result.planning.expected_shape["bead_types"])
        self.assertIn("superseded_by", result.planning.expected_shape["relations"])
        request = spy.call_args.kwargs["request"]
        self.assertEqual("causal", request["intent"])
        self.assertEqual("prefer_grounded", request["grounding_mode"])
        self.assertEqual(
            {"turn_sources": True, "max_beads": 8, "adjacent_before": 1, "adjacent_after": 1},
            request["hydration"],
        )

    def test_high_effort_uses_larger_k_and_hydration(self):
        raw = {"ok": True, "results": [], "chains": [{}]}
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw) as spy:
            result = recall("why did the deploy fail last week?", effort="high")

        request = spy.call_args.kwargs["request"]
        self.assertEqual(20, request["k"])
        self.assertEqual(
            {"turn_sources": True, "max_beads": 16, "adjacent_before": 2, "adjacent_after": 2},
            request["hydration"],
        )
        self.assertEqual("relative", result.planning.expected_shape["time_range_hint"])
        self.assertIn("causal", result.tier_path)

    def test_request_overrides_win(self):
        with patch("core_memory.retrieval.agent.memory_execute", return_value={"ok": True, "results": []}) as spy:
            recall(
                "redis",
                effort="medium",
                k=4,
                grounding_mode="search_only",
                hydration={"turn_sources": True, "max_beads": 2},
            )

        request = spy.call_args.kwargs["request"]
        self.assertEqual(4, request["k"])
        self.assertEqual("search_only", request["grounding_mode"])
        self.assertEqual({"turn_sources": True, "max_beads": 2}, request["hydration"])

    def test_dynamic_is_reserved(self):
        with self.assertRaisesRegex(ValueError, "reserved for a future"):
            recall("redis", effort="dynamic")


if __name__ == "__main__":
    unittest.main()
