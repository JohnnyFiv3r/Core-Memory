import unittest
from pathlib import Path


class TestPersistenceShimImportAudit(unittest.TestCase):
    def test_runtime_and_scripts_avoid_legacy_persistence_shims(self):
        root = Path(__file__).resolve().parents[1]
        allow = {
            root / "core_memory" / "io_utils.py",
            root / "core_memory" / "archive_index.py",
            root / "core_memory" / "events.py",
        }
        offenders = []

        for p in list((root / "core_memory").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if any(tok in txt for tok in [
                "from core_memory.io_utils import",
                "from core_memory.archive_index import",
                "from core_memory.events import",
                "import core_memory.io_utils",
                "import core_memory.archive_index",
                "import core_memory.events",
            ]):
                if p not in allow:
                    offenders.append(str(p.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Legacy persistence shim imports remain: {offenders}")


if __name__ == "__main__":
    unittest.main()
