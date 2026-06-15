import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.integrations.mcp.registry import call_tool
from core_memory.integrations.mcp.tools.capture_session import capture_session_handler
from core_memory.integrations.mcp.tools.ingest import ingest_handler


class MCPIngestHandlerTests(unittest.TestCase):
    def test_ingests_json_messages_through_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            transcript = Path(td) / "chat.json"
            transcript.write_text(json.dumps({"messages": [
                {"role": "user", "content": "Remember that the build uses MCP 1.27.1."},
                {"role": "assistant", "content": "Noted; MCP is pinned below 2."},
            ]}), encoding="utf-8")
            out = ingest_handler({"root": str(root), "path": str(transcript), "from": "json", "session_prefix": "test"})
        self.assertTrue(out["ok"])
        self.assertEqual("json", out["format"])
        self.assertEqual(2, out["turns_ingested"])
        self.assertEqual("test:chat", out["session_id"])
        self.assertIn("raw", out)

    def test_ingests_markdown_speaker_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            transcript = Path(td) / "chat.md"
            transcript.write_text("User: hello\ncontinued\n\nAssistant: hi there\n", encoding="utf-8")
            out = call_tool("ingest", {"root": str(root), "path": str(transcript)})
        self.assertTrue(out["ok"])
        self.assertEqual("markdown", out["format"])
        self.assertEqual(2, out["turns_ingested"])

    def test_rejects_unreadable_path(self):
        out = ingest_handler({"path": "/definitely/not/here.json"})
        self.assertFalse(out["ok"])
        self.assertEqual("cm.path_not_readable", out["error"]["code"])

    def test_rejects_transcript_without_user_assistant_shape(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "bad.json"
            transcript.write_text(json.dumps([{"role": "system", "content": "rules"}]), encoding="utf-8")
            out = ingest_handler({"path": str(transcript), "from": "json"})
        self.assertFalse(out["ok"])
        self.assertEqual("cm.parser_aborted", out["error"]["code"])

    def test_registry_exposes_real_ingest_handler(self):
        out = call_tool("ingest", {})
        self.assertFalse(out["ok"])
        self.assertNotEqual("mcp_tool_not_implemented", out.get("error"))
        self.assertEqual("cm.path_not_readable", out["error"]["code"])

    def test_registry_accepts_inline_turns(self):
        with tempfile.TemporaryDirectory() as td:
            out = call_tool("ingest", {
                "root": str(Path(td) / "store"),
                "transcript_id": "inline-demo",
                "turns": [
                    {"role": "user", "content": "MCP protocol ingest accepts inline turns."},
                    {"role": "assistant", "content": "Inline transcript ingest is routed canonically."},
                ],
            })
        self.assertTrue(out["ok"])
        self.assertEqual("inline", out["format"])
        self.assertEqual(2, out["turns_ingested"])

    def test_capture_session_defaults_to_supported_end_only_flush(self):
        with patch(
            "core_memory.integrations.mcp.tools.capture_session.ingest_handler",
            return_value={"ok": True, "session_id": "s", "turns_ingested": 1},
        ) as spy:
            out = capture_session_handler({"turns": [{"role": "user", "content": "hello"}]})

        self.assertTrue(out["ok"])
        payload = spy.call_args.args[0]
        self.assertEqual("group", payload["mode"])
        self.assertEqual("session_sync", payload["session_prefix"])
        self.assertEqual("end_only", payload["flush_policy"])

    def test_sync_transcript_snapshot_adds_hash_and_snapshot_metadata(self):
        turns = [
            {"role": "user", "content": "Remember that V1 uses transcript snapshots."},
            {"role": "assistant", "content": "Recorded as a snapshot."},
        ]
        with patch(
            "core_memory.integrations.mcp.tools.sync_transcript_snapshot.ingest_handler",
            return_value={"ok": True, "session_id": "s", "turns_ingested": 2, "bead_ids": []},
        ) as spy:
            out = call_tool(
                "sync_transcript_snapshot",
                {
                    "turns": turns,
                    "user_opted_in": True,
                    "source_client": "chatgpt",
                    "conversation_label": "capture-v1",
                    "snapshot_reason": "milestone",
                },
            )

        self.assertTrue(out["ok"])
        self.assertEqual("sync_transcript_snapshot", out["tool"])
        self.assertEqual("full", out["snapshot_mode"])
        self.assertEqual(64, len(out["transcript_hash"]))
        payload = spy.call_args.args[0]
        self.assertEqual("end_only", payload["flush_policy"])
        self.assertEqual("group", payload["mode"])
        self.assertEqual("transcript_snapshot", payload["session_prefix"])
        self.assertEqual(turns, payload["turns"])
        metadata = payload["metadata"]
        self.assertEqual("chatgpt", metadata["source_client"])
        self.assertEqual("chatgpt", metadata["source_system"])
        self.assertEqual("mcp_tool", metadata["capture_surface"])
        self.assertEqual("milestone", metadata["snapshot_reason"])
        self.assertEqual("capture-v1", metadata["conversation_label"])
        self.assertEqual(out["transcript_hash"], metadata["transcript_hash"])
        self.assertTrue(metadata["user_opted_in"])

    def test_sync_transcript_snapshot_rejects_missing_opt_in(self):
        out = call_tool(
            "sync_transcript_snapshot",
            {"turns": [{"role": "user", "content": "sync maybe"}]},
        )

        self.assertFalse(out["ok"])
        self.assertEqual("cm.snapshot_opt_in_required", out["error"]["code"])

    def test_sync_transcript_snapshot_rejects_opt_out(self):
        out = call_tool(
            "sync_transcript_snapshot",
            {"turns": [{"role": "user", "content": "do not sync"}], "user_opted_in": False},
        )

        self.assertFalse(out["ok"])
        self.assertEqual("cm.snapshot_opt_in_required", out["error"]["code"])

    def test_sync_transcript_snapshot_checkpoint_fallback(self):
        with patch(
            "core_memory.integrations.mcp.tools.sync_transcript_snapshot.ingest_handler",
            return_value={"ok": True, "session_id": "s", "turns_ingested": 2, "bead_ids": []},
        ) as spy:
            out = call_tool(
                "sync_transcript_snapshot",
                {
                    "recent_turns": [{"role": "user", "content": "The full transcript is too long."}],
                    "user_opted_in": True,
                    "checkpoint_summary": "We agreed to keep capture per-turn and add snapshots.",
                    "decisions": ["Do not overload capture()."],
                    "snapshot_reason": "before_compaction",
                },
            )

        self.assertTrue(out["ok"])
        self.assertEqual("checkpoint", out["snapshot_mode"])
        payload = spy.call_args.args[0]
        self.assertEqual(2, len(payload["turns"]))
        self.assertEqual("checkpoint", payload["metadata"]["snapshot_mode"])
        self.assertEqual("model_authored", payload["metadata"]["checkpoint_kind"])


if __name__ == "__main__":
    unittest.main()
