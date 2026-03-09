import unittest
from pathlib import Path


class TestP8CRetrievalPathPurity(unittest.TestCase):
    def test_runtime_and_integration_surfaces_do_not_import_memory_skill_directly(self):
        root = Path(__file__).resolve().parents[1]
        core = root / "core_memory"

        # Allow direct memory_skill imports only in explicit compatibility/internal modules.
        allow_direct = {
            core / "memory_skill" / "__init__.py",
            core / "cli.py",
            core / "tools" / "memory.py",
            core / "tools" / "memory_search.py",
        }

        offenders: list[str] = []
        for py in core.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "from core_memory.memory_skill import" in text or "import core_memory.memory_skill" in text:
                if py not in allow_direct:
                    offenders.append(str(py.relative_to(root)))

        self.assertEqual([], offenders, msg=f"Non-canonical direct memory_skill imports found: {offenders}")


if __name__ == "__main__":
    unittest.main()
