import unittest
from pathlib import Path

from core_memory.runtime import event_schemas as runtime_event_schemas
from core_memory.schema import event_schemas as canonical_event_schemas


_CANONICAL_LEGACY_PAIRS = [
    ("CRAWLER_UPDATE", "CRAWLER_UPDATE_LEGACY"),
    ("FLUSH_CHECKPOINT", "FLUSH_CHECKPOINT_LEGACY"),
    ("FLUSH_REPORT", "FLUSH_REPORT_LEGACY"),
    ("HEALTH_REPORT", "HEALTH_REPORT_LEGACY"),
    ("MEMORY_EVENT", "MEMORY_EVENT_LEGACY"),
    ("TURN_ENVELOPE", "TURN_ENVELOPE_LEGACY"),
]


class TestEventImportMigrationGuard(unittest.TestCase):
    def test_runtime_event_schema_import_path_reexports_canonical_schema(self):
        names = [name for pair in _CANONICAL_LEGACY_PAIRS for name in pair]
        for name in names:
            self.assertEqual(getattr(canonical_event_schemas, name), getattr(runtime_event_schemas, name))
        self.assertIs(canonical_event_schemas.is_flush_report, runtime_event_schemas.is_flush_report)
        self.assertIs(canonical_event_schemas.is_flush_checkpoint, runtime_event_schemas.is_flush_checkpoint)
        self.assertIs(canonical_event_schemas.is_crawler_update, runtime_event_schemas.is_crawler_update)

    def test_event_schema_constants_separate_emit_namespace_from_legacy_read_namespace(self):
        for canonical_name, legacy_name in _CANONICAL_LEGACY_PAIRS:
            canonical = getattr(canonical_event_schemas, canonical_name)
            legacy = getattr(canonical_event_schemas, legacy_name)

            self.assertTrue(canonical.startswith("core-memory."), canonical_name)
            self.assertFalse(canonical.startswith("openclaw.memory."), canonical_name)
            self.assertTrue(legacy.startswith("openclaw.memory."), legacy_name)
            self.assertNotEqual(canonical, legacy)

    def test_event_schema_read_helpers_accept_canonical_and_legacy_rows(self):
        rows = [
            {"schema": canonical_event_schemas.FLUSH_REPORT},
            {"schema": canonical_event_schemas.FLUSH_REPORT_LEGACY},
            {"schema": canonical_event_schemas.FLUSH_CHECKPOINT},
            {"schema": canonical_event_schemas.FLUSH_CHECKPOINT_LEGACY},
            {"schema": canonical_event_schemas.CRAWLER_UPDATE},
            {"schema": canonical_event_schemas.CRAWLER_UPDATE_LEGACY},
            {"schema": canonical_event_schemas.MEMORY_EVENT},
        ]

        self.assertEqual(
            [
                canonical_event_schemas.FLUSH_REPORT,
                canonical_event_schemas.FLUSH_REPORT_LEGACY,
            ],
            [row["schema"] for row in rows if canonical_event_schemas.is_flush_report(str(row.get("schema") or ""))],
        )
        self.assertEqual(
            [
                canonical_event_schemas.FLUSH_CHECKPOINT,
                canonical_event_schemas.FLUSH_CHECKPOINT_LEGACY,
            ],
            [row["schema"] for row in rows if canonical_event_schemas.is_flush_checkpoint(str(row.get("schema") or ""))],
        )
        self.assertEqual(
            [
                canonical_event_schemas.CRAWLER_UPDATE,
                canonical_event_schemas.CRAWLER_UPDATE_LEGACY,
            ],
            [row["schema"] for row in rows if canonical_event_schemas.is_crawler_update(str(row.get("schema") or ""))],
        )

    def test_core_runtime_uses_event_modules_not_sidecar_imports(self):
        root = Path(__file__).resolve().parents[1]
        core = root / "core_memory"

        # canonical event modules may contain transitional wording during migration.
        allow_sidecar_imports = {
            core / "event_state.py",
            core / "event_ingress.py",
            core / "event_worker.py",
        }

        offenders: list[str] = []
        for py in core.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "sidecar" in text and py not in allow_sidecar_imports:
                if "from .sidecar" in text or "from core_memory.sidecar" in text or "sidecar_worker" in text or "sidecar_hook" in text:
                    offenders.append(str(py.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Non-allowed sidecar imports remain: {offenders}")

    def test_tests_use_event_imports_not_sidecar_imports(self):
        root = Path(__file__).resolve().parents[1]
        tests_dir = root / "tests"

        allow = {
            tests_dir / "test_event_import_migration_guard.py",
        }

        offenders: list[str] = []
        for py in tests_dir.rglob("test_*.py"):
            text = py.read_text(encoding="utf-8")
            if py in allow:
                continue
            if "from core_memory.sidecar" in text or "sidecar_worker" in text or "sidecar_hook" in text:
                offenders.append(str(py.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Tests still importing sidecar surfaces: {offenders}")


if __name__ == "__main__":
    unittest.main()
