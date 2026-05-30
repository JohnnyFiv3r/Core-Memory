"""Tests for N-speaker group transcript ingest (#10A).

Covers:
- Group mode produces multi-row envelopes without role collapse
- Each turn row carries the original speaker label
- Windowing splits utterances into correct number of envelopes
- Unknown roles map to 'other' (not raise)
- speaker_attribution resolves all speakers in group envelopes
- Dyadic path unchanged (regression)
- unsupported_mode raises ValueError
- ingest_transcript passes mode/window_size through
"""
import unittest

from core_memory.transcript_ingest import normalize_transcript_payload


def _group_payload(**overrides):
    base = {
        "transcript_id": "test-group",
        "session_id": "sess-group",
        "mode": "group",
        "turns": [
            {"speaker": "alice", "role": "user", "content": "I think we should drop Kubernetes."},
            {"speaker": "bob", "role": "user", "content": "Agreed, it's too complex."},
            {"speaker": "carol", "role": "moderator", "content": "Let's vote on it."},
        ],
    }
    base.update(overrides)
    return base


class TestGroupModeParsing(unittest.TestCase):
    def test_group_mode_produces_envelopes(self):
        result = normalize_transcript_payload(_group_payload())
        self.assertTrue(result.get("ok"))
        self.assertGreater(len(result.get("envelopes") or []), 0)

    def test_group_mode_preserves_all_speakers(self):
        result = normalize_transcript_payload(_group_payload())
        env = result["envelopes"][0]
        speakers_in_turns = [t["speaker"] for t in env["turns"]]
        self.assertIn("alice", speakers_in_turns)
        self.assertIn("bob", speakers_in_turns)
        self.assertIn("carol", speakers_in_turns)

    def test_group_mode_no_dyadic_collapse(self):
        """All 3 utterances in one window — no collapse to user/assistant pairs."""
        result = normalize_transcript_payload(_group_payload(window_size=10))
        self.assertEqual(len(result["envelopes"]), 1)
        self.assertEqual(len(result["envelopes"][0]["turns"]), 3)

    def test_unknown_role_maps_to_other(self):
        """'moderator' role maps to 'other', not raise."""
        result = normalize_transcript_payload(_group_payload())
        env = result["envelopes"][0]
        carol_row = next(t for t in env["turns"] if t["speaker"] == "carol")
        self.assertEqual(carol_row["role"], "other")

    def test_known_role_preserved(self):
        result = normalize_transcript_payload(_group_payload())
        env = result["envelopes"][0]
        alice_row = next(t for t in env["turns"] if t["speaker"] == "alice")
        self.assertEqual(alice_row["role"], "user")

    def test_metadata_mode_is_group(self):
        result = normalize_transcript_payload(_group_payload())
        env = result["envelopes"][0]
        self.assertEqual(env["metadata"].get("mode"), "group")

    def test_metadata_speakers_list(self):
        result = normalize_transcript_payload(_group_payload())
        env = result["envelopes"][0]
        speakers = env["metadata"].get("speakers") or []
        self.assertIn("alice", speakers)
        self.assertIn("bob", speakers)
        self.assertIn("carol", speakers)

    def test_turns_received_count(self):
        result = normalize_transcript_payload(_group_payload())
        self.assertEqual(result["turns_received"], 3)

    def test_result_mode_field(self):
        result = normalize_transcript_payload(_group_payload())
        self.assertEqual(result.get("mode"), "group")


class TestGroupModeWindowing(unittest.TestCase):
    def _make_turns(self, n: int) -> list[dict]:
        speakers = ["alice", "bob", "carol", "dave"]
        return [
            {"speaker": speakers[i % 4], "role": "user", "content": f"message {i}"}
            for i in range(n)
        ]

    def test_single_window(self):
        turns = self._make_turns(5)
        result = normalize_transcript_payload(
            {"transcript_id": "t", "mode": "group", "turns": turns, "window_size": 10}
        )
        self.assertEqual(len(result["envelopes"]), 1)
        self.assertEqual(len(result["envelopes"][0]["turns"]), 5)

    def test_exact_window_boundary(self):
        turns = self._make_turns(10)
        result = normalize_transcript_payload(
            {"transcript_id": "t", "mode": "group", "turns": turns, "window_size": 5}
        )
        self.assertEqual(len(result["envelopes"]), 2)
        self.assertEqual(len(result["envelopes"][0]["turns"]), 5)
        self.assertEqual(len(result["envelopes"][1]["turns"]), 5)

    def test_uneven_window(self):
        turns = self._make_turns(12)
        result = normalize_transcript_payload(
            {"transcript_id": "t", "mode": "group", "turns": turns, "window_size": 5}
        )
        self.assertEqual(len(result["envelopes"]), 3)
        self.assertEqual(len(result["envelopes"][2]["turns"]), 2)

    def test_window_turn_ids_are_unique(self):
        turns = self._make_turns(15)
        result = normalize_transcript_payload(
            {"transcript_id": "t", "mode": "group", "turns": turns, "window_size": 5}
        )
        ids = [env["turn_id"] for env in result["envelopes"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_window_size_is_10(self):
        turns = self._make_turns(25)
        result = normalize_transcript_payload(
            {"transcript_id": "t", "mode": "group", "turns": turns}
        )
        self.assertEqual(len(result["envelopes"]), 3)


class TestGroupModeEdgeCases(unittest.TestCase):
    def test_empty_turns_raises(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_transcript_payload({"transcript_id": "t", "mode": "group", "turns": []})
        self.assertIn("turns_required", str(ctx.exception))

    def test_all_empty_content_raises(self):
        turns = [{"speaker": "alice", "role": "user", "content": ""}]
        with self.assertRaises(ValueError) as ctx:
            normalize_transcript_payload({"transcript_id": "t", "mode": "group", "turns": turns})
        self.assertIn("group_transcript_empty", str(ctx.exception))

    def test_unsupported_mode_raises(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_transcript_payload(
                {"transcript_id": "t", "mode": "multi", "turns": [{"role": "user", "content": "hi"}]}
            )
        self.assertIn("unsupported_mode", str(ctx.exception))

    def test_non_dict_items_skipped(self):
        turns = [
            "not a dict",
            {"speaker": "alice", "role": "user", "content": "valid"},
        ]
        result = normalize_transcript_payload({"transcript_id": "t", "mode": "group", "turns": turns})
        self.assertEqual(result["turns_received"], 1)


class TestDyadicRegressionInGroupMode(unittest.TestCase):
    """Dyadic path must be unchanged."""

    def test_dyadic_still_pairs(self):
        result = normalize_transcript_payload({
            "transcript_id": "t",
            "mode": "dyadic",
            "turns": [
                {"role": "user", "speaker": "alice", "content": "question"},
                {"role": "assistant", "speaker": "bot", "content": "answer"},
            ],
        })
        self.assertEqual(len(result["envelopes"]), 1)
        self.assertEqual(len(result["envelopes"][0]["turns"]), 2)
        self.assertEqual(result.get("mode"), "dyadic")

    def test_dyadic_unknown_role_raises(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_transcript_payload({
                "transcript_id": "t",
                "mode": "dyadic",
                "turns": [{"role": "moderator", "content": "hi"}],
            })
        self.assertIn("unsupported_role", str(ctx.exception))

    def test_default_mode_is_dyadic(self):
        result = normalize_transcript_payload({
            "transcript_id": "t",
            "turns": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        })
        self.assertEqual(result.get("mode"), "dyadic")


class TestGroupSpeakerResolution(unittest.TestCase):
    """_resolve_envelope_speakers resolves all speakers in group mode."""

    def test_all_speakers_get_attribution(self):
        import tempfile
        from pathlib import Path
        from core_memory.transcript_ingest import _resolve_envelope_speakers

        result = normalize_transcript_payload(_group_payload(window_size=10))
        envelopes = result["envelopes"]

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads").mkdir(parents=True)
            resolved = _resolve_envelope_speakers(tmp, envelopes, source_system="slack")

        meta = resolved[0]["metadata"]
        attribution = meta.get("speaker_attribution") or []
        speakers_resolved = {e["speaker_observed"] for e in attribution}
        self.assertIn("alice", speakers_resolved)
        self.assertIn("bob", speakers_resolved)
        self.assertIn("carol", speakers_resolved)

    def test_group_envelopes_have_attribution_for_each_turn(self):
        import tempfile
        from pathlib import Path
        from core_memory.transcript_ingest import _resolve_envelope_speakers

        result = normalize_transcript_payload(_group_payload(window_size=10))
        envelopes = result["envelopes"]

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".beads").mkdir(parents=True)
            resolved = _resolve_envelope_speakers(tmp, envelopes, source_system="slack")

        meta = resolved[0]["metadata"]
        attribution = meta.get("speaker_attribution") or []
        # One entry per unique speaker in the window
        self.assertEqual(len(attribution), 3)


class TestIngestTranscriptGroupMode(unittest.TestCase):
    """ingest_transcript passes mode/window_size into normalize_transcript_payload."""

    def test_ingest_transcript_group_mode_parameter(self):
        from unittest.mock import patch
        from core_memory.transcript_ingest import ingest_transcript

        captured: list[dict] = []

        def fake_normalize(payload, *, max_turns=500):
            captured.append(dict(payload))
            return {"ok": True, "transcript_id": "t", "session_id": "s",
                    "flush_policy": "none", "mode": "group", "turns_received": 0,
                    "turns_paired": 0, "warnings": [], "envelopes": []}

        with patch("core_memory.transcript_ingest.normalize_transcript_payload", side_effect=fake_normalize):
            ingest_transcript(
                root="/tmp",
                turns=[{"speaker": "a", "role": "user", "content": "hi"}],
                mode="group",
                window_size=5,
            )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["mode"], "group")
        self.assertEqual(captured[0]["window_size"], 5)


if __name__ == "__main__":
    unittest.main()
