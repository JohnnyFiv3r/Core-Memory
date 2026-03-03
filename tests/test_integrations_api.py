import tempfile
import unittest
from pathlib import Path

from core_memory.integrations.api import emit_turn_finalized


class TestIntegrationsApi(unittest.TestCase):
    def test_emit_turn_finalized_emits_event_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            event_id = emit_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                user_query="u",
                assistant_final="a",
            )
            self.assertTrue(event_id.startswith("mev-"))

    def test_origin_memory_pass_is_guarded_default_nonfatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            out = emit_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                user_query="u",
                assistant_final="a",
                origin="MEMORY_PASS",
            )
            self.assertIsNone(out)

    def test_origin_memory_pass_is_guarded_strict(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            with self.assertRaises(ValueError):
                emit_turn_finalized(
                    root=root,
                    session_id="s1",
                    turn_id="t1",
                    transaction_id="tx1",
                    user_query="u",
                    assistant_final="a",
                    origin="MEMORY_PASS",
                    strict=True,
                )

    def test_privacy_ref_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td) / "memory")
            emit_turn_finalized(
                root=root,
                session_id="s1",
                turn_id="t1",
                transaction_id="tx1",
                user_query="u",
                assistant_final="very sensitive assistant output",
                metadata={"store_full_text": False},
            )
            events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
            row = events_file.read_text(encoding="utf-8").splitlines()[-1]
            self.assertIn("assistant_final_ref", row)


if __name__ == "__main__":
    unittest.main()
