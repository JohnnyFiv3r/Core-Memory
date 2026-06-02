"""Tests for source-system MCP ingest adapters (#10B).

Covers:
- Slack: JSON export format → group ingest with source_system="slack"
- Discord: DiscordChatExporter JSON → group ingest with source_system="discord"
- Zoom: VTT format → group ingest with source_system="zoom:{recording_id}"
- Otter: JSON diarization → source_system="otter:{recording_id}"
- Each adapter: empty/malformed input → proper error
- Recording-scope: same SPEAKER_NN label from two different recordings gets
  different source_system tags (no false merge in entity registry)
"""
import unittest

from core_memory.integrations.mcp.tools.ingest_slack import _parse_slack_payload
from core_memory.integrations.mcp.tools.ingest_discord import _parse_discord_payload
from core_memory.integrations.mcp.tools.ingest_zoom import _parse_vtt, _parse_otter


# ── Slack ─────────────────────────────────────────────────────────────────────

class TestSlackParser(unittest.TestCase):
    def _messages(self):
        return [
            {"type": "message", "user": "U12345ABCDE", "text": "Hello team", "ts": "1000.0001", "username": "alice"},
            {"type": "message", "user": "U99999XXXXX", "text": "Hi Alice!", "ts": "1001.0001", "username": "bob"},
            {"type": "message_changed", "user": "U12345ABCDE", "text": "Updated"},  # non-message type filtered
        ]

    def test_parses_user_id_as_speaker(self):
        turns = _parse_slack_payload(self._messages())
        self.assertEqual(turns[0]["speaker"], "U12345ABCDE")
        self.assertEqual(turns[1]["speaker"], "U99999XXXXX")

    def test_filters_non_message_types(self):
        turns = _parse_slack_payload(self._messages())
        # message_changed type filtered out
        self.assertEqual(len(turns), 2)

    def test_text_maps_to_content(self):
        turns = _parse_slack_payload(self._messages())
        self.assertEqual(turns[0]["content"], "Hello team")

    def test_all_roles_are_user(self):
        turns = _parse_slack_payload(self._messages())
        self.assertTrue(all(t["role"] == "user" for t in turns))

    def test_list_format(self):
        turns = _parse_slack_payload(self._messages())
        self.assertEqual(len(turns), 2)

    def test_dict_with_messages_key(self):
        turns = _parse_slack_payload({"messages": self._messages()})
        self.assertEqual(len(turns), 2)

    def test_empty_returns_empty(self):
        turns = _parse_slack_payload([])
        self.assertEqual(turns, [])

    def test_missing_content_skipped(self):
        turns = _parse_slack_payload([{"type": "message", "user": "U1234", "text": ""}])
        self.assertEqual(turns, [])


class TestSlackHandler(unittest.TestCase):
    def test_inline_messages(self):
        from core_memory.integrations.mcp.tools.ingest_slack import ingest_slack_handler
        from unittest.mock import patch

        messages = [
            {"type": "message", "user": "U111", "text": "first"},
            {"type": "message", "user": "U222", "text": "second"},
        ]

        with patch("core_memory.integrations.mcp.tools.ingest_slack.ingest_transcript") as mock:
            mock.return_value = {"ok": True, "turns_received": 2, "turns_paired": 1,
                                  "session_id": "slack:test", "ingested": []}
            result = ingest_slack_handler({"messages": messages, "root": "/tmp"})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("source_system"), "slack")
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["mode"], "group")
        self.assertEqual(call_kwargs["metadata"]["source_system"], "slack")

    def test_no_input_returns_error(self):
        from core_memory.integrations.mcp.tools.ingest_slack import ingest_slack_handler
        result = ingest_slack_handler({})
        self.assertFalse(result.get("ok"))

    def test_empty_messages_returns_error(self):
        from core_memory.integrations.mcp.tools.ingest_slack import ingest_slack_handler
        result = ingest_slack_handler({"messages": []})
        self.assertFalse(result.get("ok"))


# ── Discord ───────────────────────────────────────────────────────────────────

class TestDiscordParser(unittest.TestCase):
    def _messages(self):
        return [
            {
                "id": "1234567890",
                "type": "Default",
                "content": "Hello everyone",
                "author": {"id": "987654321", "username": "johnnyfiv3r", "discriminator": "1234"},
                "timestamp": "2024-01-01T12:00:00.000+00:00",
            },
            {
                "id": "1234567891",
                "type": "Default",
                "content": "Hey!",
                "author": {"id": "111222333", "username": "alice", "discriminator": "0000"},
                "timestamp": "2024-01-01T12:01:00.000+00:00",
            },
        ]

    def test_stable_author_id_as_speaker(self):
        turns = _parse_discord_payload(self._messages())
        # author_id is the canonical speaker label for stable identity resolution
        self.assertEqual(turns[0]["speaker"], "discord:987654321")

    def test_stable_author_id_regardless_of_discriminator(self):
        turns = _parse_discord_payload(self._messages())
        # author_id wins even when discriminator is present or zero
        self.assertEqual(turns[1]["speaker"], "discord:111222333")

    def test_content_preserved(self):
        turns = _parse_discord_payload(self._messages())
        self.assertEqual(turns[0]["content"], "Hello everyone")

    def test_all_roles_user(self):
        turns = _parse_discord_payload(self._messages())
        self.assertTrue(all(t["role"] == "user" for t in turns))

    def test_dict_with_messages_key(self):
        turns = _parse_discord_payload({"messages": self._messages()})
        self.assertEqual(len(turns), 2)

    def test_empty_content_skipped(self):
        turns = _parse_discord_payload([
            {"type": "Default", "content": "", "author": {"username": "alice"}}
        ])
        self.assertEqual(turns, [])


class TestDiscordHandler(unittest.TestCase):
    def test_inline_messages(self):
        from core_memory.integrations.mcp.tools.ingest_discord import ingest_discord_handler
        from unittest.mock import patch

        messages = [
            {"type": "Default", "content": "Hello", "author": {"username": "alice", "id": "123"}},
            {"type": "Default", "content": "World", "author": {"username": "bob", "id": "456"}},
        ]

        with patch("core_memory.integrations.mcp.tools.ingest_discord.ingest_transcript") as mock:
            mock.return_value = {"ok": True, "turns_received": 2, "turns_paired": 1,
                                  "session_id": "discord:test", "ingested": []}
            result = ingest_discord_handler({"messages": messages, "root": "/tmp"})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("source_system"), "discord")
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["mode"], "group")
        self.assertEqual(call_kwargs["metadata"]["source_system"], "discord")

    def test_no_input_returns_error(self):
        from core_memory.integrations.mcp.tools.ingest_discord import ingest_discord_handler
        result = ingest_discord_handler({})
        self.assertFalse(result.get("ok"))


# ── Zoom/Otter ────────────────────────────────────────────────────────────────

class TestZoomVTTParser(unittest.TestCase):
    _VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Jane Smith: Hello everyone, let's get started.

2
00:00:04.000 --> 00:00:07.000
John Doe: Thanks for the quick turnaround.

3
00:00:08.000 --> 00:00:11.000
SPEAKER_00: I have a question about the timeline.
"""

    def test_parses_named_speakers(self):
        turns = _parse_vtt(self._VTT)
        speakers = [t["speaker"] for t in turns]
        self.assertIn("Jane Smith", speakers)
        self.assertIn("John Doe", speakers)

    def test_parses_diarization_label(self):
        turns = _parse_vtt(self._VTT)
        speakers = [t["speaker"] for t in turns]
        self.assertIn("SPEAKER_00", speakers)

    def test_content_extracted(self):
        turns = _parse_vtt(self._VTT)
        self.assertEqual(turns[0]["content"], "Hello everyone, let's get started.")

    def test_empty_vtt_returns_empty(self):
        turns = _parse_vtt("WEBVTT\n\n")
        self.assertEqual(turns, [])

    def test_all_roles_user(self):
        turns = _parse_vtt(self._VTT)
        self.assertTrue(all(t["role"] == "user" for t in turns))


class TestOtterParser(unittest.TestCase):
    def _data(self):
        return {
            "transcript": [
                {"speaker": "SPEAKER_0", "start_time": 1.0, "end_time": 3.5, "text": "Hello"},
                {"speaker": "SPEAKER_1", "start_time": 4.0, "end_time": 6.0, "text": "Hi there"},
            ]
        }

    def test_parses_speakers(self):
        turns = _parse_otter(self._data())
        self.assertEqual(turns[0]["speaker"], "SPEAKER_0")
        self.assertEqual(turns[1]["speaker"], "SPEAKER_1")

    def test_parses_content(self):
        turns = _parse_otter(self._data())
        self.assertEqual(turns[0]["content"], "Hello")

    def test_empty_text_skipped(self):
        turns = _parse_otter({"transcript": [{"speaker": "S0", "text": ""}]})
        self.assertEqual(turns, [])


class TestZoomHandler(unittest.TestCase):
    _VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Alice: First comment.

2
00:00:04.000 --> 00:00:06.000
Bob: Second comment.
"""

    def test_vtt_inline_text(self):
        from core_memory.integrations.mcp.tools.ingest_zoom import ingest_zoom_handler
        from unittest.mock import patch

        with patch("core_memory.integrations.mcp.tools.ingest_zoom.ingest_transcript") as mock:
            mock.return_value = {"ok": True, "turns_received": 2, "turns_paired": 1,
                                  "session_id": "zoom:rec-001", "ingested": []}
            result = ingest_zoom_handler({
                "text": self._VTT,
                "format": "vtt",
                "recording_id": "rec-001",
                "root": "/tmp",
            })

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("source_system"), "zoom:rec-001")
        self.assertEqual(result.get("recording_id"), "rec-001")
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["metadata"]["source_system"], "zoom:rec-001")
        self.assertEqual(call_kwargs["mode"], "group")

    def test_recording_scope_isolation(self):
        """Two recordings produce different source_system values — no speaker cross-merge."""
        from core_memory.integrations.mcp.tools.ingest_zoom import ingest_zoom_handler
        from unittest.mock import patch

        calls = []

        def capture(**kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "turns_received": 1, "turns_paired": 1,
                    "session_id": "zoom:x", "ingested": []}

        with patch("core_memory.integrations.mcp.tools.ingest_zoom.ingest_transcript", side_effect=capture):
            ingest_zoom_handler({"text": self._VTT, "format": "vtt", "recording_id": "rec-A", "root": "/tmp"})
            ingest_zoom_handler({"text": self._VTT, "format": "vtt", "recording_id": "rec-B", "root": "/tmp"})

        self.assertEqual(len(calls), 2)
        sys_a = calls[0]["metadata"]["source_system"]
        sys_b = calls[1]["metadata"]["source_system"]
        self.assertEqual(sys_a, "zoom:rec-A")
        self.assertEqual(sys_b, "zoom:rec-B")
        self.assertNotEqual(sys_a, sys_b)

    def test_no_input_returns_error(self):
        from core_memory.integrations.mcp.tools.ingest_zoom import ingest_zoom_handler
        result = ingest_zoom_handler({})
        self.assertFalse(result.get("ok"))


# ── Generic ingest_handler group-mode passthrough ────────────────────────────

class TestGenericIngestHandlerGroupMode(unittest.TestCase):
    def test_group_mode_skips_dyadic_validation(self):
        """Generic ingest_handler with mode=group should not require user+assistant."""
        from core_memory.integrations.mcp.tools.ingest import ingest_handler
        from unittest.mock import patch

        turns = [
            {"speaker": "alice", "role": "participant", "content": "Hello"},
            {"speaker": "bob", "role": "participant", "content": "Hi"},
        ]

        with patch("core_memory.integrations.mcp.tools.ingest.ingest_transcript") as mock:
            mock.return_value = {"ok": True, "turns_received": 2, "turns_paired": 1,
                                  "session_id": "s", "ingested": []}
            result = ingest_handler({"turns": turns, "mode": "group", "root": "/tmp"})

        self.assertTrue(result.get("ok"))
        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["mode"], "group")

    def test_source_system_passed_through(self):
        from core_memory.integrations.mcp.tools.ingest import ingest_handler
        from unittest.mock import patch

        turns = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        with patch("core_memory.integrations.mcp.tools.ingest.ingest_transcript") as mock:
            mock.return_value = {"ok": True, "turns_received": 2, "turns_paired": 1,
                                  "session_id": "s", "ingested": []}
            ingest_handler({"turns": turns, "source_system": "discord", "root": "/tmp"})

        call_kwargs = mock.call_args[1]
        self.assertEqual(call_kwargs["metadata"]["source_system"], "discord")


# ── Real end-to-end integration tests (no mocks) ────────────────────────────

class TestSlackIntegration(unittest.TestCase):
    """Full ingest path — no mock; exercises timestamp conversion bug fix."""

    def test_slack_unix_epoch_timestamps_accepted(self):
        import tempfile
        from core_memory.integrations.mcp.tools.ingest_slack import ingest_slack_handler
        with tempfile.TemporaryDirectory() as td:
            result = ingest_slack_handler({
                "root": td,
                "session_id": "slack-integ",
                "messages": [
                    {"user": "U111AAA", "username": "alice", "text": "We should use Postgres", "ts": "1700000001.0"},
                    {"user": "U222BBB", "username": "bob", "text": "Agreed", "ts": "1700000002.000000"},
                ],
            })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result.get('error')}")

    def test_slack_no_timestamps_accepted(self):
        import tempfile
        from core_memory.integrations.mcp.tools.ingest_slack import ingest_slack_handler
        with tempfile.TemporaryDirectory() as td:
            result = ingest_slack_handler({
                "root": td,
                "session_id": "slack-nots",
                "messages": [
                    {"user": "U111AAA", "text": "Message without timestamp"},
                ],
            })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result.get('error')}")


class TestZoomIntegration(unittest.TestCase):
    """Full ingest path — no mock; exercises VTT relative-timecode handling."""

    _VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Alice: We should use Postgres.

2
00:00:04.000 --> 00:00:06.000
Bob: Agreed, better ACID guarantees.
"""

    def test_vtt_relative_timecodes_do_not_raise(self):
        import tempfile
        from core_memory.integrations.mcp.tools.ingest_zoom import ingest_zoom_handler
        with tempfile.TemporaryDirectory() as td:
            result = ingest_zoom_handler({
                "root": td,
                "text": self._VTT,
                "format": "vtt",
                "recording_id": "rec-001",
            })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result.get('error')}")

    def test_otter_no_timestamps_do_not_raise(self):
        import tempfile
        from core_memory.integrations.mcp.tools.ingest_zoom import ingest_zoom_handler
        with tempfile.TemporaryDirectory() as td:
            result = ingest_zoom_handler({
                "root": td,
                "data": {
                    "transcript": [
                        {"speaker": "SPEAKER_00", "text": "Hello"},
                        {"speaker": "SPEAKER_01", "text": "World"},
                    ]
                },
                "format": "otter",
                "recording_id": "otter-001",
            })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result.get('error')}")


class TestDiscordIntegration(unittest.TestCase):
    """Full ingest path — no mock; Discord uses ISO 8601 timestamps (already correct)."""

    def test_discord_iso_timestamps_accepted(self):
        import tempfile
        from core_memory.integrations.mcp.tools.ingest_discord import ingest_discord_handler
        with tempfile.TemporaryDirectory() as td:
            result = ingest_discord_handler({
                "root": td,
                "session_id": "discord-integ",
                "messages": [
                    {
                        "id": "1", "type": "Default",
                        "content": "Hello everyone",
                        "timestamp": "2023-11-15T10:30:00.000+00:00",
                        "author": {"username": "alice", "id": "187198988139290624"},
                    },
                    {
                        "id": "2", "type": "Default",
                        "content": "Good morning",
                        "timestamp": "2023-11-15T10:31:00.000+00:00",
                        "author": {"username": "bob", "id": "987654321098765432"},
                    },
                ],
            })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result.get('error')}")


if __name__ == "__main__":
    unittest.main()
