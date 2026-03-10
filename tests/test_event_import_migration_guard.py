import unittest
from pathlib import Path


class TestEventImportMigrationGuard(unittest.TestCase):
    def test_core_runtime_uses_event_modules_not_sidecar_imports(self):
        root = Path(__file__).resolve().parents[1]
        core = root / "core_memory"

        # sidecar modules themselves + canonical event aliases are allowed to reference sidecar.
        allow_sidecar_imports = {
            core / "sidecar.py",
            core / "sidecar_hook.py",
            core / "sidecar_worker.py",
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
            tests_dir / "test_sidecar_contracts.py",
            tests_dir / "test_sidecar_hook.py",
            tests_dir / "test_sidecar_worker.py",
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
