import json
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.rolling_record_store import write_rolling_records
from core_memory.persistence.store import MemoryStore
from core_memory.runtime.engine import process_session_start
from core_memory.write_pipeline.continuity_injection import load_continuity_injection


class TestSessionStartBoundary(unittest.TestCase):
    def test_process_session_start_idempotent_for_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            MemoryStore(str(root))
            write_rolling_records(
                str(root),
                records=[{"type": "decision", "title": "Carry this", "summary": ["context"]}],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )

            out1 = process_session_start(root=str(root), session_id="s1", source="test", max_items=20)
            out2 = process_session_start(root=str(root), session_id="s1", source="test", max_items=20)

            self.assertTrue(out1.get("ok"))
            self.assertTrue(out1.get("created"))
            self.assertTrue(out2.get("ok"))
            self.assertFalse(out2.get("created"))
            self.assertEqual(out1.get("bead_id"), out2.get("bead_id"))

            idx = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            beads = [b for b in (idx.get("beads") or {}).values() if str((b or {}).get("session_id") or "") == "s1"]
            session_start = [b for b in beads if str((b or {}).get("type") or "") == "session_start"]
            self.assertEqual(1, len(session_start))

    def test_continuity_load_is_pure_read_no_new_beads_even_with_session_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            MemoryStore(str(root))
            idx_file = root / ".beads" / "index.json"

            before = json.loads(idx_file.read_text(encoding="utf-8"))
            before_count = len((before.get("beads") or {}))

            out = load_continuity_injection(
                str(root),
                max_items=20,
                session_id="s1",
                ensure_session_start=True,
            )
            self.assertEqual("none", out.get("authority"))

            after = json.loads(idx_file.read_text(encoding="utf-8"))
            after_count = len((after.get("beads") or {}))
            self.assertEqual(before_count, after_count)

    def test_continuity_read_does_not_mark_semantic_dirty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            MemoryStore(str(root))
            dirty = root / ".beads" / "events" / "semantic-dirty.jsonl"

            # create one session_start boundary first (expected to dirty once)
            process_session_start(root=str(root), session_id="s1", source="test", max_items=20)
            before_lines = []
            if dirty.exists():
                before_lines = [ln for ln in dirty.read_text(encoding="utf-8").splitlines() if ln.strip()]

            load_continuity_injection(str(root), max_items=20)

            after_lines = []
            if dirty.exists():
                after_lines = [ln for ln in dirty.read_text(encoding="utf-8").splitlines() if ln.strip()]

            self.assertEqual(len(before_lines), len(after_lines))

    def test_session_start_snapshot_filters_prior_session_start_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "memory"
            MemoryStore(str(root))

            write_rolling_records(
                str(root),
                records=[
                    {"type": "session_start", "title": "Session start", "summary": ["old snapshot"]},
                    {"type": "decision", "title": "Carry substantive context", "summary": ["real context"]},
                ],
                meta={},
                included_bead_ids=[],
                excluded_bead_ids=[],
            )

            out = process_session_start(root=str(root), session_id="s2", source="test", max_items=20)
            self.assertTrue(out.get("ok"))

            idx = json.loads((root / ".beads" / "index.json").read_text(encoding="utf-8"))
            bead = (idx.get("beads") or {}).get(str(out.get("bead_id") or "")) or {}
            detail = str(bead.get("detail") or "")
            self.assertIn("Carry substantive context", detail)
            self.assertNotIn("[session_start] Session start", detail)


if __name__ == "__main__":
    unittest.main()
