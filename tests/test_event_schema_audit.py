from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core_memory.persistence.event_schema_audit import EVENT_SCHEMA_AUDIT_SCHEMA, audit_event_schemas
from core_memory.schema.event_schemas import (
    FLUSH_REPORT,
    FLUSH_REPORT_LEGACY,
    MEMORY_EVENT_LEGACY,
    TURN_ENVELOPE,
)


def _append_jsonl(path: Path, *rows: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, str):
                handle.write(row + "\n")
            else:
                handle.write(json.dumps(row) + "\n")


def _snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestEventSchemaAudit(unittest.TestCase):
    def test_audit_detects_canonical_and_legacy_event_schema_rows_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="cm-event-schema-audit-") as td:
            root = Path(td)
            _append_jsonl(
                root / ".beads" / "events" / "flush-checkpoints.jsonl",
                {"schema": FLUSH_REPORT, "stage": "committed", "session_id": "s1"},
                {"schema": FLUSH_REPORT_LEGACY, "stage": "legacy", "session_id": "s1"},
            )
            _append_jsonl(
                root / ".beads" / "events" / "memory-events.jsonl",
                {
                    "event": {"schema": MEMORY_EVENT_LEGACY, "event_id": "mev-legacy", "session_id": "s2"},
                    "envelope": {"schema": TURN_ENVELOPE, "session_id": "s2", "turn_id": "t1"},
                },
                {"schema": "core_memory.unrelated_event.v1"},
                "{not-json",
            )
            before = _snapshot(root)

            out = audit_event_schemas(root)

            self.assertEqual(before, _snapshot(root))
            self.assertTrue(out["ok"])
            self.assertTrue(out["read_only"])
            self.assertFalse(out["mutation"]["performed"])
            self.assertEqual(EVENT_SCHEMA_AUDIT_SCHEMA, out["schema"])
            self.assertTrue(out["has_legacy_event_schema_rows"])
            self.assertEqual(2, out["legacy_row_count"])
            self.assertEqual(2, out["legacy_match_count"])
            self.assertEqual(2, out["canonical_row_count"])
            self.assertEqual(2, out["canonical_match_count"])
            self.assertEqual(1, out["invalid_jsonl_line_count"])
            self.assertEqual({FLUSH_REPORT_LEGACY: 1, MEMORY_EVENT_LEGACY: 1}, out["legacy_schema_counts"])
            self.assertEqual({FLUSH_REPORT: 1, TURN_ENVELOPE: 1}, out["canonical_schema_counts"])
            self.assertEqual({"core_memory.unrelated_event.v1": 1}, out["other_schema_counts"])
            self.assertEqual("$.event.schema", out["legacy_rows"][1]["field_path"])
            self.assertEqual("mev-legacy", out["legacy_rows"][1]["id"])

    def test_audit_missing_root_is_read_only_empty_report(self):
        with tempfile.TemporaryDirectory(prefix="cm-event-schema-audit-") as td:
            root = Path(td) / "missing"

            out = audit_event_schemas(root)

            self.assertFalse(root.exists())
            self.assertTrue(out["ok"])
            self.assertFalse(out["events_dir_exists"])
            self.assertEqual(0, out["files_scanned"])
            self.assertEqual(0, out["legacy_row_count"])

    def test_cli_event_schema_audit_does_not_initialize_store_projection(self):
        cwd = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="cm-event-schema-audit-cli-") as td:
            root = Path(td) / "memory"
            _append_jsonl(
                root / ".beads" / "events" / "flush-checkpoints.jsonl",
                {"schema": FLUSH_REPORT_LEGACY, "stage": "legacy", "session_id": "s1"},
            )

            out = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "core_memory.cli",
                    "--root",
                    str(root),
                    "ops",
                    "event-schema-audit",
                    "--limit",
                    "1",
                ],
                cwd=str(cwd),
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, out.returncode, out.stderr)
            payload = json.loads(out.stdout)
            self.assertEqual(EVENT_SCHEMA_AUDIT_SCHEMA, payload["schema"])
            self.assertEqual(1, payload["legacy_row_count"])
            self.assertEqual([FLUSH_REPORT_LEGACY], list(payload["legacy_schema_counts"]))
            self.assertFalse((root / ".beads" / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
