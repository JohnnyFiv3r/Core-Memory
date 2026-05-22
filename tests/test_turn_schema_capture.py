import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory import Memory, Turn, capture, process_turn_finalized, emit_turn_finalized
from core_memory.integrations.mcp.typed_write import write_turn_finalized


class TestTurnSchemaCapture(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "CORE_MEMORY_BEAD_FIELD_JUDGE_MODE": "heuristic",
                "CORE_MEMORY_AGENT_AUTHORED_MODE": "off",
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_turn_validation(self):
        with self.assertRaisesRegex(ValueError, "speaker"):
            Turn(speaker="", content="x")
        with self.assertRaisesRegex(ValueError, "role"):
            Turn(speaker="user", role="reviewer", content="x")
        self.assertEqual("", Turn(speaker="user", role="user", content="").content)

    def test_memory_capture_shortcut_records_speakers(self):
        with tempfile.TemporaryDirectory() as td:
            m = Memory(td, self_id="john@example.com")
            out = m.capture(user="hi", assistant="hey", session_id="s1", turn_id="t1")
            self.assertTrue(out.get("ok"))
            event = ((((out.get("emitted") or {}).get("payload") or {}).get("envelope") or {}))
            self.assertEqual(["user", "assistant"], event.get("speakers"))
            self.assertEqual("hi", event.get("user_query"))
            self.assertEqual("hey", event.get("assistant_final"))

    def test_memory_capture_attributed_shortcut_records_speakers(self):
        with tempfile.TemporaryDirectory() as td:
            out = Memory(td).capture(
                user="hi",
                assistant="hey",
                as_user="john@example.com",
                as_assistant="claude",
                session_id="s1",
                turn_id="t1",
            )
            event = ((((out.get("emitted") or {}).get("payload") or {}).get("envelope") or {}))
            self.assertEqual(["john@example.com", "claude"], event.get("speakers"))

    def test_explicit_multispeaker_turn_list(self):
        with tempfile.TemporaryDirectory() as td:
            out = capture(
                root=td,
                session_id="meeting",
                turn_id="t1",
                turns=[
                    Turn(speaker="alice", role="user", content="I prefer Postgres"),
                    Turn(speaker="bob", role="other", content="SQLite is simpler"),
                    Turn(speaker="claude", role="assistant", content="Decision depends on ops risk"),
                ],
            )
            event = ((((out.get("emitted") or {}).get("payload") or {}).get("envelope") or {}))
            self.assertEqual(["alice", "bob", "claude"], event.get("speakers"))
            self.assertEqual("I prefer Postgres", event.get("user_query"))
            self.assertEqual("Decision depends on ops risk", event.get("assistant_final"))

    def test_other_only_turn_uses_turn_text_for_semantic_bead(self):
        with tempfile.TemporaryDirectory() as td:
            out = process_turn_finalized(
                root=td,
                session_id="meeting",
                turn_id="t-other-1",
                turns=[Turn(speaker="alice", role="other", content="Alice adopted a rescue dog named Pixel.")],
            )
            self.assertTrue(out.get("ok"))
            from core_memory.persistence.store import MemoryStore
            idx = MemoryStore(td)._read_json(Path(td) / ".beads" / "index.json")
            beads = list((idx.get("beads") or {}).values())
            self.assertEqual(1, len(beads))
            self.assertIn("Alice adopted", beads[0].get("title"))
            self.assertNotEqual("assistant turn", beads[0].get("title"))

    def test_session_context_scopes_capture(self):
        with tempfile.TemporaryDirectory() as td:
            with Memory(td).session("planning") as s:
                out = s.capture(user="u", assistant="a", turn_id="t1")
            event = ((((out.get("emitted") or {}).get("payload") or {}).get("event") or {}))
            self.assertEqual("planning", event.get("session_id"))

    def test_legacy_runtime_kwargs_raise_migration_error(self):
        with self.assertRaisesRegex(TypeError, "no longer accepts"):
            process_turn_finalized(root=".", session_id="s", turn_id="t", user_query="u", assistant_final="a")
        with self.assertRaisesRegex(TypeError, "no longer accepts"):
            emit_turn_finalized(root=".", session_id="s", turn_id="t", user_query="u", assistant_final="a")

    def test_mcp_legacy_fields_return_protocol_error(self):
        out = write_turn_finalized(root=".", session_id="s", turn_id="t", user_query="u", assistant_final="a")
        self.assertFalse(out.get("ok"))
        self.assertEqual("legacy_turn_fields_removed", out.get("error"))


if __name__ == "__main__":
    unittest.main()
