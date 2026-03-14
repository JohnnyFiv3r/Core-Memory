import unittest
from pathlib import Path


class TestP8CRetrievalPathPurity(unittest.TestCase):
    def test_runtime_and_integration_surfaces_do_not_import_legacy_memory_skill(self):
        root = Path(__file__).resolve().parents[1]
        core = root / "core_memory"

        offenders: list[str] = []
        for py in core.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "core_memory.memory_skill" in text or "from .memory_skill" in text:
                offenders.append(str(py.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Legacy memory_skill imports found: {offenders}")


if __name__ == "__main__":
    unittest.main()
