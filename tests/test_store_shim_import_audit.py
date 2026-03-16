import unittest
from pathlib import Path


class TestStoreShimImportAudit(unittest.TestCase):
    def test_core_runtime_code_avoids_legacy_store_shim_imports(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []

        for p in list((root / "core_memory").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if "from core_memory.store import" in txt or "import core_memory.store" in txt:
                offenders.append(str(p.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Legacy store shim imports remain in runtime/scripts: {offenders}")


if __name__ == "__main__":
    unittest.main()
