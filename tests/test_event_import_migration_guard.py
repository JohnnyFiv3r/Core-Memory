import unittest
from pathlib import Path

from core_memory.runtime import event_schemas as runtime_event_schemas
from core_memory.schema import event_schemas as canonical_event_schemas


class TestEventImportMigrationGuard(unittest.TestCase):
    def test_runtime_event_schema_import_path_reexports_canonical_schema(self):
        names = [
            "CRAWLER_UPDATE",
            "CRAWLER_UPDATE_LEGACY",
            "FLUSH_CHECKPOINT",
            "FLUSH_CHECKPOINT_LEGACY",
            "FLUSH_REPORT",
            "FLUSH_REPORT_LEGACY",
            "HEALTH_REPORT",
            "HEALTH_REPORT_LEGACY",
            "MEMORY_EVENT",
            "MEMORY_EVENT_LEGACY",
            "TURN_ENVELOPE",
            "TURN_ENVELOPE_LEGACY",
        ]
        for name in names:
            self.assertEqual(getattr(canonical_event_schemas, name), getattr(runtime_event_schemas, name))
        self.assertIs(canonical_event_schemas.is_flush_report, runtime_event_schemas.is_flush_report)
        self.assertIs(canonical_event_schemas.is_flush_checkpoint, runtime_event_schemas.is_flush_checkpoint)
        self.assertIs(canonical_event_schemas.is_crawler_update, runtime_event_schemas.is_crawler_update)

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
