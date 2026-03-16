import json
import os
import tempfile
import unittest
from pathlib import Path

from core_memory.runtime.write_trigger_dispatcher import emit_write_trigger, dispatch_write_trigger


class TestWriteTriggersRetiredExtract(unittest.TestCase):
    def test_extract_trigger_is_retired(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS"] = "1"
            try:
                event_id = emit_write_trigger(
                    root=td,
                    trigger_type="extract_beads",
                    source="extract-beads.py",
                    payload={"session_id_arg": "abc"},
                )
                event = {
                    "event_id": event_id,
                    "trigger_type": "extract_beads",
                    "payload": {"session_id_arg": "abc"},
                }
                out = dispatch_write_trigger(root=td, event=event, workspace_root=td)
                self.assertFalse(out.get("ok"))
                self.assertEqual("extract_path_retired", out.get("error"))

                p = Path(td) / ".beads" / "events" / "write-trigger-processed.jsonl"
                self.assertTrue(p.exists())
                rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assertTrue(any(r.get("status") == "retired" for r in rows))
            finally:
                os.environ.pop("CORE_MEMORY_ALLOW_LEGACY_WRITE_TRIGGERS", None)


if __name__ == "__main__":
    unittest.main()
