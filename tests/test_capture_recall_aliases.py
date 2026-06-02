import unittest
from unittest.mock import patch

import core_memory


class TestCaptureAlias(unittest.TestCase):
    def test_capture_is_public_wrapper(self):
        self.assertTrue(callable(core_memory.capture))

    def test_capture_forwards_shortcut_to_runtime_turns(self):
        expected = {"ok": True, "processed": 1, "turn_id": "t1"}
        metadata = {
            "retrieved_beads": ["b1", "b2"],
            "used_memory": True,
            "crawler_updates": [{"type": "decision", "title": "Keep alias thin"}],
        }
        with patch("core_memory.runtime.engine.process_turn_finalized", return_value=expected) as spy:
            out = core_memory.capture(
                root=".",
                session_id="s1",
                turn_id="t1",
                user="q",
                assistant="a",
                metadata=metadata,
            )

        self.assertEqual(expected, out)
        kwargs = spy.call_args.kwargs
        self.assertEqual(metadata, kwargs["metadata"])
        self.assertEqual("user", kwargs["turns"][0].speaker)
        self.assertEqual("assistant", kwargs["turns"][1].speaker)

    def test_capture_forwards_all_public_kwargs(self):
        expected = {"ok": True}
        policy = object()
        tools_trace = [{"tool": "search"}]
        mesh_trace = [{"agent": "child"}]
        with patch("core_memory.runtime.engine.process_turn_finalized", return_value=expected) as spy:
            out = core_memory.capture(
                root="/tmp/memory",
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                trace_id="trace1",
                user="",
                assistant="",
                trace_depth=2,
                origin="TEST",
                tools_trace=tools_trace,
                mesh_trace=mesh_trace,
                window_turn_ids=["t0", "t1"],
                window_bead_ids=["b0"],
                metadata={"used_memory": False},
                policy=policy,
            )

        self.assertEqual(expected, out)
        kwargs = spy.call_args.kwargs
        self.assertEqual("tx1", kwargs["transaction_id"])
        self.assertEqual("trace1", kwargs["trace_id"])
        self.assertEqual("", kwargs["turns"][0].content)
        self.assertEqual("", kwargs["turns"][1].content)
        self.assertEqual(2, kwargs["trace_depth"])
        self.assertEqual("TEST", kwargs["origin"])
        self.assertIs(tools_trace, kwargs["tools_trace"])
        self.assertIs(mesh_trace, kwargs["mesh_trace"])
        self.assertEqual(["t0", "t1"], kwargs["window_turn_ids"])
        self.assertEqual(["b0"], kwargs["window_bead_ids"])
        self.assertEqual({"used_memory": False}, kwargs["metadata"])
        self.assertIs(policy, kwargs["policy"])


class TestRecallWrapper(unittest.TestCase):
    def test_recall_string_query_returns_recall_result(self):
        raw = {"ok": True, "results": []}
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw) as spy:
            out = core_memory.recall(" redis timeouts ", root="/tmp/memory", explain=False)

        self.assertIsInstance(out, core_memory.RecallResult)
        self.assertEqual("empty", out.status)
        spy.assert_called_once()
        self.assertEqual("/tmp/memory", spy.call_args.kwargs["root"])
        self.assertFalse(spy.call_args.kwargs["explain"])
        self.assertEqual(
            {
                "raw_query": "redis timeouts",
                "intent": "remember",
                "k": 10,
                "effort": "medium",
                "grounding_mode": "prefer_grounded",
                "hydration": {"turn_sources": True, "max_beads": 8, "adjacent_before": 1, "adjacent_after": 1},
            },
            spy.call_args.kwargs["request"],
        )

    def test_recall_dict_request_preserves_explicit_values(self):
        raw = {"ok": True, "results": []}
        request = {"raw_query": "why redis", "intent": "causal", "k": 3, "grounding_mode": "search_only"}
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw) as spy:
            out = core_memory.recall(request, effort="high")

        self.assertEqual("high", out.planning.selected_effort)
        expected_request = request | {
            "effort": "high",
            "hydration": {"turn_sources": True, "max_beads": 16, "adjacent_before": 2, "adjacent_after": 2},
            # "why ..." parses to causal structural hints (soft; reorders chains only).
            "facets": {"structural_hint_relations": ["caused_by", "led_to", "supports"]},
        }
        self.assertEqual(expected_request, spy.call_args.kwargs["request"])

    def test_recall_valid_effort_and_speaker_pass_through(self):
        raw = {"ok": True, "results": []}
        with patch("core_memory.retrieval.agent.memory_execute", return_value=raw) as spy:
            out = core_memory.recall("redis", effort="low", speaker="alice", k=5, topic="infra")

        self.assertEqual("low", out.planning.selected_effort)
        self.assertEqual(
            {
                "raw_query": "redis",
                "intent": "remember",
                "k": 5,
                "effort": "low",
                "grounding_mode": "search_only",
                "speaker": "alice",
                "facets": {"metadata": {"speaker": "alice"}},
                "topic": "infra",
            },
            spy.call_args.kwargs["request"],
        )

    def test_recall_rejects_invalid_effort(self):
        with self.assertRaisesRegex(ValueError, "recall effort must be one of"):
            core_memory.recall("redis", effort="giant")

    def test_recall_rejects_dynamic_for_now(self):
        with self.assertRaisesRegex(ValueError, "reserved for a future"):
            core_memory.recall("redis", effort="dynamic")

    def test_recall_rejects_empty_string_query(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            core_memory.recall("   ")

    def test_recall_rejects_non_string_non_dict_query(self):
        with self.assertRaisesRegex(TypeError, "string or dict"):
            core_memory.recall(["redis"])


if __name__ == "__main__":
    unittest.main()
